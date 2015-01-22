# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from datetime import timedelta
from decimal import Decimal

from trytond.model import ModelView, Workflow, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval, Or
from trytond.transaction import Transaction

from trytond.modules.production.production import BOM_CHANGES
from trytond.modules.production_supply_request.supply_request \
    import prepare_write_vals

__all__ = ['Prescription', 'Production', 'SupplyRequestLine']
__metaclass__ = PoolMeta

PRESCRIPTION_CHANGES = BOM_CHANGES[:] + ['prescription']


class SupplyRequestLine:
    __name__ = 'stock.supply_request.line'

    prescription_required = fields.Boolean('Prescription Required')

    @classmethod
    def __setup__(cls):
        super(SupplyRequestLine, cls).__setup__()
        cls._error_messages.update({
                'to_warehouse_farm_line_not_available': ('The specified '
                    'destination warehouse in supply request "%s" is not '
                    'configured as a farm for none specie.'),
                })

    @fields.depends('product')
    def on_change_with_prescription_required(self):
        return (True if self.product and self.product.prescription_template
            else False)

    def get_move(self):
        move = super(SupplyRequestLine, self).get_move()
        if self.prescription_required:
            with Transaction().set_user(0, set_context=True):
                prescription = self.get_prescription()
                prescription.save()
                if prescription.template:
                    Prescription.set_template([prescription])
            move.prescription = prescription
        return move

    def get_prescription(self):
        pool = Pool()
        Date = pool.get('ir.date')
        FarmLine = pool.get('farm.specie.farm_line')

        farm_lines = FarmLine.search([
                ('farm', '=', self.request.to_warehouse.id),
                ])
        if not farm_lines:
            self.raise_user_error('to_warehouse_farm_line_not_available',
                self.request.rec_name)

        Prescription = pool.get('farm.prescription')
        prescription = Prescription()
        prescription.specie = farm_lines[0].specie
        prescription.date = Date.today()
        prescription.farm = self.request.to_warehouse
        prescription.delivery_date = self.delivery_date
        prescription.feed_product = self.product
        prescription.quantity = self.quantity
        # prescription.animals
        # prescription.animal_groups
        prescription.origin = self

        if self.product.prescription_template:
            prescription.template = self.product.prescription_template

        return prescription

    def get_production(self):
        production = super(SupplyRequestLine, self).get_production()
        if self.move.prescription:
            production.prescription = self.move.prescription
        return production

    def _production_bom(self):
        pool = Pool()
        Bom = pool.get('production.bom')

        product_bom = super(SupplyRequestLine, self)._production_bom()
        if product_bom:
            current_version = Bom.search([
                    ('master_bom', '=', product_bom.master_bom),
                    ], limit=1)
            if current_version:
                return current_version[0]
        return None


class Production:
    __name__ = 'production'

    prescription = fields.Many2One('farm.prescription', 'Prescription',
        domain=[
            ('feed_product', '=', Eval('product')),
            ],
        states={
            'readonly': Or(~Eval('state').in_(['request', 'draft']),
                Eval('from_supply_request', False)),
            'invisible': ~Eval('product'),
            },
        depends=['warehouse', 'product', 'state', 'from_supply_request'])

    @classmethod
    def __setup__(cls):
        super(Production, cls).__setup__()
        for fname in ('product', 'bom', 'uom', 'quantity'):
            field = getattr(cls, fname)
            for fname2 in ('prescription', 'origin'):
                field.on_change.add(fname2)

        cls._error_messages.update({
                'from_supply_request_invalid_prescription': (
                    'The Production "%(production)s" has the Supply Request '
                    '"%(origin)s" as origin which requires prescription, but '
                    'it has not prescription or it isn\'t the origin\'s '
                    'prescription.'),
                'invalid_input_move_prescription': (
                    'The Input Move "%(move)s" of Production "%(production)s" '
                    'is related to a different prescription than the '
                    'production.'),
                'missing_input_moves_from_prescription': (
                    'The Production "%(production)s" is related to a '
                    'prescription but the next lines of this prescription '
                    'doesn\'t appear in the Input Moves of production: '
                    '%(missing_lines)s.'),
                'no_changes_allowed_prescription_confirmed': (
                    'You can\'t change the Quantity nor Uom of production '
                    '"%(production)s" because it has the prescription '
                    '"%(prescription)s" related and it is already confirmed.'),
                'prescription_not_confirmed': ('To assign the production '
                    '"%(production)s" the prescription "%(prescription)s", '
                    'which is related to it, must to be Confirmed or Done.'),
                })

    @fields.depends(methods=['bom'])
    def on_change_prescription(self):
        return self.explode_bom()

    @classmethod
    def validate(cls, productions):
        super(Production, cls).validate(productions)
        for production in productions:
            production.check_prescription()

    def check_prescription(self):
        if self.from_supply_request and (
                self.origin.prescription_required and not self.prescription or
                self.prescription != self.origin.move.prescription):
            self.raise_user_error('from_supply_request_invalid_prescription', {
                    'production': self.rec_name,
                    'origin': self.origin.request.rec_name,
                    })
        if not self.prescription:
            return

        prescription_lines = self.prescription.lines[:]
        for input_move in self.inputs:
            if (input_move.prescription and
                    input_move.prescription != self.prescription):
                self.raise_user_error('invalid_input_move_prescription', {
                        'move': input_move.rec_name,
                        'production': self.rec_name,
                        })
            if input_move.prescription:
                prescription_lines.remove(input_move.origin)
        if prescription_lines:
            self.raise_user_error('missing_input_moves_from_prescription', {
                    'production': self.rec_name,
                    'missing_lines': ", ".join(l.rec_name
                        for l in prescription_lines),
                    })

    def explode_bom(self):
        pool = Pool()
        Uom = pool.get('product.uom')
        Template = pool.get('product.template')
        Product = pool.get('product.product')

        changes = super(Production, self).explode_bom()
        if not changes or not self.prescription:
            return changes
        # Set the prescription to the main output move
        if 'outputs' in changes:
            if 'add' in changes['outputs']:
                for _, output_vals in changes['outputs']['add']:
                    if output_vals.get('product') == self.product.id:
                        output_vals['prescription'] = self.prescription.id

        if not self.prescription.lines:
            return changes

        if self.warehouse:
            storage_location = self.warehouse.storage_location
        else:
            storage_location = None

        inputs = changes['inputs']
        extra_cost = Decimal(0)

        factor = self.prescription.get_factor_change_quantity_uom(
            self.quantity, self.uom)
        for prescription_line in self.prescription.lines:
            if factor is not None:
                prescription_line.quantity = (
                    prescription_line.compute_quantity(factor))
            values = self._explode_prescription_line_values(storage_location,
                self.location, self.company, prescription_line)
            if values:
                inputs['add'].append((-1, values))
                quantity = Uom.compute_qty(prescription_line.unit,
                    prescription_line.quantity,
                    prescription_line.product.default_uom)
                extra_cost += (Decimal(str(quantity)) *
                    prescription_line.product.cost_price)

        if hasattr(Product, 'cost_price'):
            digits = Product.cost_price.digits
        else:
            digits = Template.cost_price.digits
        for _, output in changes['outputs']['add']:
            quantity = output.get('quantity')
            if quantity:
                output['unit_price'] += Decimal(
                    extra_cost / Decimal(str(quantity))
                    ).quantize(Decimal(str(10 ** -digits[1])))

        changes['cost'] += extra_cost
        return changes

    def _explode_prescription_line_values(self, from_location, to_location,
            company, line):
        pool = Pool()
        Move = pool.get('stock.move')

        move = self._move(from_location, to_location, company, line.product,
            line.unit, line.quantity)
        # move.from_location = from_location.id if from_location else None
        # move.to_location = to_location.id if to_location else None
        move.unit_price_required = move.on_change_with_unit_price_required()
        move.prescription = line.prescription
        move.origin = line

        values = {}
        for field_name, field in Move._fields.iteritems():
            try:
                value = getattr(move, field_name)
            except AttributeError:
                continue
            if value and field._type in ('many2one', 'one2one'):
                values[field_name] = value.id
                values[field_name + '.rec_name'] = value.rec_name
            else:
                values[field_name] = value
        return values

    def _assign_reservation(self, main_output):
        pool = Pool()
        Prescription = pool.get('farm.prescription')

        reservation = self.origin.move
        if getattr(main_output, 'lot', False) and reservation.prescription:
            with Transaction().set_user(0, set_context=True):
                prescription = Prescription(reservation.prescription.id)
                prescription.feed_lot = main_output.lot
                prescription.save()
        return super(Production, self)._assign_reservation(main_output)

    @classmethod
    def assign(cls, productions):
        for production in productions:
            if production.prescription:
                if production.prescription.state not in ('confirmed', 'done'):
                    cls.raise_user_error('prescription_not_confirmed', {
                            'prescription': production.prescription.rec_name,
                            'production': production.rec_name,
                            })
        super(Production, cls).assign(productions)

    @classmethod
    def done(cls, productions):
        pool = Pool()
        Prescription = pool.get('farm.prescription')

        super(Production, cls).done(productions)
        prescriptions_todo = []
        for production in productions:
            if production.prescription:
                expiry_period = production.prescription.expiry_period
                if expiry_period:
                    for output in production.outputs:
                        if output.lot:
                            output.lot.expiry_date = (output.efective_date +
                                timedelta(days=expiry_period))
                            output.lot.save()
                prescriptions_todo.append(production.prescription)
        if prescriptions_todo:
            Prescription.done(prescriptions_todo)

    @classmethod
    def write(cls, *args):
        pool = Pool()
        Prescription = pool.get('farm.prescription')

        production_ids_qty_uom_modified = []
        actions = iter(args)
        for productions, values in zip(actions, actions):
            if 'quantity' in values or 'uom' in values:
                for production in productions:
                    prescription = production.prescription
                    if prescription and prescription.state != 'draft':
                        cls.raise_user_error(
                            'no_changes_allowed_prescription_confirmed', {
                                'production': production.rec_name,
                                'prescription': prescription.rec_name,
                                })
                    elif prescription:
                        production_ids_qty_uom_modified.append(production.id)

        super(Production, cls).write(*args)

        for production in cls.browse(production_ids_qty_uom_modified):
            factor = production.prescription.get_factor_change_quantity_uom(
                production.quantity, production.uom)
            if factor is not None:
                for line in prescription.lines:
                    line.quantity = line.compute_quantity(factor)
                    line.save()
                with Transaction().set_user(0, set_context=True):
                    prescription = Prescription(prescription.id)
                    prescription.quantity = quantity
                    prescription.save()


class Prescription:
    __name__ = 'farm.prescription'

    origin_production = fields.Function(fields.Many2One('production',
            'Origin Production'),
        'on_change_with_origin_production')

    @classmethod
    def __setup__(cls):
        super(Prescription, cls).__setup__()
        for fname in ('farm', 'delivery_date', 'feed_product', 'feed_lot',
                'quantity'):
            field = getattr(cls, fname)
            field.states['readonly'] = Or(field.states['readonly'],
                Bool(Eval('origin_production')))
            field.depends.append('origin_production')

        cls._error_messages.update({
                'cant_delete_productions_prescription': (
                    'The Prescription "%(prescription)s" is related to '
                    'Production "%(production)s". You can\'t delete it.'),
                })

    @classmethod
    def _get_origin(cls):
        res = super(Prescription, cls)._get_origin()
        return res + ['stock.supply_request.line']

    @fields.depends('origin')
    def on_change_with_origin_production(self, name=None):
        pool = Pool()
        Production = pool.get('production')
        SupplyRequestLine = pool.get('stock.supply_request.line')
        if self.origin and isinstance(self.origin, Production):
            return self.origin.id
        elif (self.origin and isinstance(self.origin, SupplyRequestLine)
                and self.origin.production):
            return self.origin.production.id

    def get_factor_change_quantity_uom(self, new_quantity, new_uom):
        Uom = Pool().get('product.uom')

        if new_uom != self.unit:
            new_quantity = Uom.compute_qty(new_uom, new_quantity,
                self.unit)
        if new_quantity != self.quantity:
            # quantity have chaned
            return new_quantity / self.quantity
        return None

    @classmethod
    @ModelView.button
    @Workflow.transition('confirmed')
    def confirm(cls, prescriptions):
        pool = Pool()
        Production = pool.get('production')

        super(Prescription, cls).confirm(prescriptions)
        for prescription in prescriptions:
            if prescription.origin_production:
                production = prescription.origin_production
                if production.state not in ('request', 'draft', 'waiting'):
                    continue
                with Transaction().set_user(0, set_context=True):
                    Production.write([production],
                        prepare_write_vals(production.explode_bom()))

    @classmethod
    def delete(cls, prescriptions):
        for prescription in prescriptions:
            if prescription.origin_production:
                cls.raise_user_error('cant_delete_productions_prescription', {
                        'prescription': prescription.rec_name,
                        'production': prescription.origin_production.rec_name,
                        })
        super(Prescription, cls).delete(prescriptions)
