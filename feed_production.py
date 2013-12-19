# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from decimal import Decimal

from trytond.model import ModelView, Workflow, fields
from trytond.pool import Pool, PoolMeta

from trytond.modules.production_supply_request.supply_request \
    import prepare_write_vals

__all__ = ['Prescription', 'Production', 'SupplyRequestLine']
__metaclass__ = PoolMeta


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

    def get_move(self):
        move = super(SupplyRequestLine, self).get_move()
        if self.prescription_required:
            prescription = self.get_prescription()
            prescription.save()
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
        prescription.date = Date.today()
        prescription.delivery_date = self.delivery_date
        prescription.specie = farm_lines[0].specie
        prescription.farm = self.request.to_warehouse
        prescription.feed_product = self.product
        prescription.quantity = self.quantity
        # prescription.animals
        # prescription.animal_groups
        prescription.origin = self
        return prescription

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

    @classmethod
    def __setup__(cls):
        super(Production, cls).__setup__()
        for fname in ('product', 'bom', 'uom', 'quantity'):
            field = getattr(cls, fname)
            for fname2 in ('from_supply_request', 'origin'):
                if fname2 not in field.on_change:
                    field.on_change.append(fname2)
                if fname2 not in field.depends:
                    field.depends.append(fname2)

        cls._error_messages.update({
                'no_changes_allowed_prescription_confirmed': (
                    'You can\'t change the Quantity nor Uom of production '
                    '"%(production)s" because it comes from a Supply Request '
                    'and is related to prescription "%(prescription)s" which '
                    'is already confirmed.'),
                })

    def explode_bom(self):
        pool = Pool()
        Uom = Pool().get('product.uom')
        Template = pool.get('product.template')
        Product = pool.get('product.product')

        changes = super(Production, self).explode_bom()
        if not changes:
            return changes
        if not self.from_supply_request:
            return changes
        prescription = self.origin.move.prescription
        if prescription:
            for name in ['inputs', 'outputs']:
                if name in changes:
                    if 'add' in changes[name]:
                        for value in changes[name]['add']:
                            value['prescription'] = prescription.id

        if (not self.origin.prescription_required or not prescription.lines):
            return changes

        if self.warehouse:
            storage_location = self.warehouse.storage_location
        else:
            storage_location = None

        inputs = changes['inputs']
        extra_cost = Decimal(0)
        prescription = self.origin.move.prescription
        for prescription_line in prescription.lines:
            values = self._explode_prescription_line_values(storage_location,
                self.location, self.company, prescription_line)
            if values:
                inputs['add'].append(values)
                quantity = Uom.compute_qty(prescription_line.unit,
                    prescription_line.quantity,
                    prescription_line.product.default_uom)
                extra_cost += (Decimal(str(quantity)) *
                    prescription_line.product.cost_price)

        if hasattr(Product, 'cost_price'):
            digits = Product.cost_price.digits
        else:
            digits = Template.cost_price.digits
        for output in changes['outputs']['add']:
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
        move.from_location = from_location.id if from_location else None
        move.to_location = to_location.id if to_location else None
        move.unit_price_required = move.on_change_with_unit_price_required()
        move.prescription = line.prescription

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
        reservation = self.origin.move
        if getattr(main_output, 'lot', False) and reservation.prescription:
            reservation.prescription.feed_lot = main_output.lot
            reservation.prescription.save()
        return super(Production, self)._assign_reservation(main_output)

    @classmethod
    def write(cls, productions, vals):
        pool = Pool()
        Uom = pool.get('product.uom')

        prescriptions_to_change = []
        if 'quantity' in vals or 'uom' in vals:
            for production in productions:
                if not production.from_supply_request:
                    continue
                prescription = production.origin.move.prescription
                if prescription and prescription.state != 'draft':
                    cls.raise_user_error(
                        'no_changes_allowed_prescription_confirmed', {
                            'production': production.rec_name,
                            'prescription': prescription.rec_name,
                            })
                elif prescription:
                    prescriptions_to_change.append((production, prescription))

        super(Production, cls).write(productions, vals)
        for (production, prescription) in prescriptions_to_change:
            quantity = vals.get('quantity', production.quantity)
            uom = vals.get('uom', production.uom)

            if uom != prescription.unit:
                quantity = Uom.compute_qty(uom, quantity,
                    prescription.unit)
            if quantity != prescription.quantity:
                factor = quantity / prescription.quantity
                for line in prescription.lines:
                    line.quantity = line.compute_quantity(factor)
                    line.save()
                prescription.quantity = quantity
                prescription.save()


class Prescription:
    __name__ = 'farm.prescription'

    @classmethod
    def _get_origin(cls):
        res = super(Prescription, cls)._get_origin()
        return res + ['stock.supply_request.line']

    @property
    def from_supply_request(self):
        pool = Pool()
        SupplyRequestLine = pool.get('stock.supply_request.line')
        return self.origin and isinstance(self.origin, SupplyRequestLine)

    @classmethod
    @ModelView.button
    @Workflow.transition('confirmed')
    def confirm(cls, prescriptions):
        pool = Pool()
        Production = pool.get('production')

        super(Prescription, cls).confirm(prescriptions)
        for prescription in prescriptions:
            if prescription.origin and prescription.from_supply_request:
                production = prescription.origin.production
                if not production or production.state not in ('request',
                        'draft', 'waiting'):
                    continue
                Production.write([production],
                    prepare_write_vals(production.explode_bom()))
