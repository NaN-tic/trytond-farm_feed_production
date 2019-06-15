# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from datetime import timedelta
from decimal import Decimal

from trytond.model import ModelView, Workflow, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval, Or
from trytond.transaction import Transaction
from trytond.exceptions import UserError
from trytond.i18n import gettext

from trytond.modules.production.production import BOM_CHANGES
from trytond.modules.production_supply_request.supply_request \
    import prepare_write_vals

__all__ = ['Prescription', 'Production', 'SupplyRequestLine']

PRESCRIPTION_CHANGES = BOM_CHANGES[:] + ['prescription']


class SupplyRequestLine(metaclass=PoolMeta):
    __name__ = 'stock.supply_request.line'

    def get_move(self):
        pool = Pool()
        Prescription = pool.get('farm.prescription')

        move = super(SupplyRequestLine, self).get_move()
        if self.product.prescription_required:
            with Transaction().set_user(0, set_context=True):
                prescription = self.get_prescription()
                prescription.save()
                if prescription.template:
                    Prescription.set_template([prescription])
            move.prescription = prescription
            move.quantity += prescription.drug_quantity
        return move

    def get_prescription(self):
        pool = Pool()
        Date = pool.get('ir.date')
        FarmLine = pool.get('farm.specie.farm_line')

        farm_lines = FarmLine.search([
                ('farm', '=', self.request.to_warehouse.id),
                ])
        if not farm_lines:
            raise UserError(gettext('farm_feed_production.'
                    'msg_to_warehouse_farm_line_not_available',
                    request=self.request.rec_name))

        Prescription = pool.get('farm.prescription')
        prescription = Prescription()
        prescription.specie = farm_lines[0].specie
        prescription.date = Date.today()
        prescription.farm = self.request.to_warehouse
        prescription.delivery_date = self.delivery_date
        prescription.product = self.product
        prescription.quantity = self.quantity
        # prescription.animals
        # prescription.animal_groups
        prescription.origin = self

        if self.product.prescription_template:
            prescription.template = self.product.prescription_template

        return prescription

    def get_production(self):
        with Transaction().set_context(
                avoid_production_check_prescription=True):
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


class Production(metaclass=PoolMeta):
    __name__ = 'production'

    prescription = fields.Many2One('farm.prescription', 'Prescription',
        domain=[
            ('product', '=', Eval('product')),
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

    @fields.depends(methods=['explode_bom'])
    def on_change_prescription(self):
        self.explode_bom()

    @classmethod
    def validate(cls, productions):
        super(Production, cls).validate(productions)
        for production in productions:
            production.check_prescription()

    def check_prescription(self):
        if Transaction().context.get('avoid_production_check_prescription'):
            return

        if self.from_supply_request and (
                self.origin.product.prescription_required and
                not self.prescription or
                self.prescription != self.origin.move.prescription):
            raise UserError(gettext('farm_feed_production.'
                    'msg_from_supply_request_invalid_prescription',
                    production=self.rec_name,
                    origin=self.origin.request.rec_name,
                    ))
        if not self.prescription:
            return

        prescription_lines = list(self.prescription.lines[:])
        for input_move in self.inputs:
            if (input_move.prescription and
                    input_move.prescription != self.prescription):
                raise UserError(gettext('farm_feed_production.'
                        'msg_invalid_input_move_prescription',
                        move=input_move.rec_name,
                        production=self.rec_name,
                        ))
            if input_move.prescription:
                prescription_lines.remove(input_move.origin)
        if prescription_lines:
            raise UserError(gettext('farm_feed_production.'
                    'msg_missing_input_moves_from_prescription',
                    production=self.rec_name,
                    missing_lines=", ".join(l.rec_name for l in
                        prescription_lines),
                    ))

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
                        output_vals['quantity'] += Uom.compute_qty(
                            self.prescription.unit,
                            self.prescription.drug_quantity,
                            Uom(output_vals['uom']))

        if not self.prescription.lines:
            return changes

        if self.warehouse:
            storage_location = self.warehouse.storage_location
        else:
            storage_location = None

        inputs = changes['inputs']
        extra_cost = Decimal(0)

        factor = self.prescription.get_factor_change_quantity_unit(
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
        move = self._move(from_location, to_location, company, line.product,
            line.unit, line.quantity)
        # move.from_location = from_location.id if from_location else None
        # move.to_location = to_location.id if to_location else None
        move.unit_price_required = move.on_change_with_unit_price_required()
        move.prescription = line.prescription
        move.origin = str(line)
        return move._save_values

    def _assign_reservation(self, main_output):
        pool = Pool()
        Prescription = pool.get('farm.prescription')

        reservation = self.origin.move
        if getattr(main_output, 'lot', False) and reservation.prescription:
            with Transaction().set_user(0, set_context=True):
                prescription = Prescription(reservation.prescription.id)
                prescription.lot = main_output.lot
                prescription.save()
        return super(Production, self)._assign_reservation(main_output)

    @classmethod
    def assign(cls, productions):
        for production in productions:
            if production.prescription:
                if production.prescription.state not in ('confirmed', 'done'):
                    raise UserError(gettext('farm_feed_production.'
                            'msg_prescription_not_confirmed',
                            prescription=production.prescription.rec_name,
                            production=production.rec_name,
                            ))
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
                prescription_lot = None
                for output in production.outputs:
                    if output.lot:
                        if expiry_period:
                            output.lot.expiry_date = (output.effective_date +
                                timedelta(days=expiry_period))
                            output.lot.save()
                        if output.lot.product == production.product:
                            prescription_lot = output.lot
                if prescription_lot:
                    production.prescription.lot = prescription_lot
                    production.prescription.save()
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
                        raise UserError(gettext('farm_feed_production.'
                                'msg_no_changes_allowed_prescription_confirmed',
                                production=production.rec_name,
                                prescription=prescription.rec_name,
                                ))
                    elif prescription:
                        production_ids_qty_uom_modified.append(production.id)

        super(Production, cls).write(*args)

        for production in cls.browse(production_ids_qty_uom_modified):
            factor = production.prescription.get_factor_change_quantity_unit(
                production.quantity, production.uom)
            if factor is not None:
                for line in prescription.lines:
                    line.quantity = line.compute_quantity(factor)
                    line.save()
                with Transaction().set_user(0, set_context=True):
                    prescription = Prescription(prescription.id)
                    prescription.quantity = prescription.unit.round(
                        production.quantity * factor)
                    prescription.save()


class Prescription(metaclass=PoolMeta):
    __name__ = 'farm.prescription'

    origin_production = fields.Function(fields.Many2One('production',
            'Origin Production'),
        'on_change_with_origin_production')

    @classmethod
    def __setup__(cls):
        super(Prescription, cls).__setup__()
        for fname in ('farm', 'delivery_date', 'product', 'lot', 'quantity'):
            field = getattr(cls, fname)
            field.states['readonly'] = Or(field.states['readonly'],
                Bool(Eval('origin_production')))
            field.depends.append('origin_production')

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
                raise UserError(gettext('farm_feed_production.'
                        'msg_cant_delete_productions_prescription',
                        prescription=prescription.rec_name,
                        production=prescription.origin_production.rec_name,
                        ))
        super(Prescription, cls).delete(prescriptions)
