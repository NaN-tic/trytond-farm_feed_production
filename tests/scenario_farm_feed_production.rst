Farm Feed Production Scenario
=============================

Imports::

    >>> import datetime
    >>> from dateutil.relativedelta import relativedelta
    >>> from decimal import Decimal, ROUND_HALF_EVEN
    >>> from proteus import config, Model, Wizard
    >>> from trytond.tests.tools import activate_modules
    >>> from trytond.modules.company.tests.tools import create_company, \
    ...     get_company
    >>> now = datetime.datetime.now()
    >>> today = datetime.date.today()

Activate module::

    >>> config = activate_modules('farm_feed_production')

Create company::

    >>> _ = create_company()
    >>> company = get_company()

Reload the context::

    >>> User = Model.get('res.user')
    >>> config._context = User.get_preferences(True, config.context)

Configuration production location::

    >>> Location = Model.get('stock.location')
    >>> warehouse, = Location.find([('code', '=', 'WH')])
    >>> production_location, = Location.find([('code', '=', 'PROD')])
    >>> warehouse.production_location = production_location
    >>> warehouse.save()

Configure Supply Request sequence::

    >>> StockConfiguration = Model.get('stock.configuration')
    >>> Sequence = Model.get('ir.sequence')
    >>> stock_configuration = StockConfiguration.find([])
    >>> if stock_configuration:
    ...     stock_configuration = stock_configuration[0]
    ... else:
    ...     stock_configuration = StockConfiguration()
    >>> request_sequence, = Sequence.find([
    ...         ('sequence_type.name', '=', 'Supply Request'),
    ...         ])
    >>> stock_configuration.supply_request_sequence = request_sequence
    >>> stock_configuration.default_request_from_warehouse = warehouse
    >>> stock_configuration.save()

Create products::

    >>> ProductUom = Model.get('product.uom')
    >>> unit, = ProductUom.find([('name', '=', 'Unit')])
    >>> ProductTemplate = Model.get('product.template')
    >>> Product = Model.get('product.product')
    >>> individual_template = ProductTemplate(
    ...     name='Male Pig',
    ...     default_uom=unit,
    ...     type='goods',
    ...     list_price=Decimal('40'),
    ...     cost_price=Decimal('25'))
    >>> individual_template.save()
    >>> individual_product = Product(template=individual_template)
    >>> individual_product.save()
    >>> group_template = ProductTemplate(
    ...     name='Group of Pig',
    ...     default_uom=unit,
    ...     type='goods',
    ...     list_price=Decimal('30'),
    ...     cost_price=Decimal('20'))
    >>> group_template.save()
    >>> group_product = Product(template=group_template)
    >>> group_product.save()

Create sequence::

    >>> StrictSequence = Model.get('ir.sequence.strict')
    >>> SequenceType = Model.get('ir.sequence.type')
    >>> sequence_type, = SequenceType.find([('name', '=', 'Prescription')])
    >>> prescription_sequence = StrictSequence(
    ...     name='Pig Prescriptions',
    ...     sequence_type=sequence_type,
    ...     padding=4)
    >>> prescription_sequence.save()
    >>> sequence_type, = SequenceType.find([('name', '=', 'Event Order')])
    >>> event_order_sequence = Sequence(
    ...     name='Event Order Pig Warehouse 1',
    ...     sequence_type=sequence_type,
    ...     padding=4)
    >>> event_order_sequence.save()
    >>> sequence_type, = SequenceType.find([('name', '=', 'Animal')])
    >>> individual_sequence = Sequence(
    ...     name='Individual Pig Warehouse 1',
    ...     sequence_type=sequence_type,
    ...     padding=4)
    >>> individual_sequence.save()
    >>> sequence_type, = SequenceType.find([('name', '=', 'Animal Group')])
    >>> group_sequence = Sequence(
    ...     name='Groups Pig Warehouse 1',
    ...     sequence_type=sequence_type,
    ...     padding=4)
    >>> group_sequence.save()

Prepare farm and Silo locations::

    >>> lost_found_location, = Location.find([('type', '=', 'lost_found')])
    >>> farm_storage_id, farm_input_id, farm_production_id = Location.create([{
    ...         'name': 'Farm Sorage',
    ...         'type': 'storage',
    ...         }, {
    ...         'name': 'Farm Input',
    ...         'type': 'storage',
    ...         }, {
    ...         'name': 'Farm Production',
    ...         'type': 'production',
    ...         }], config.context)
    >>> farm = Location(
    ...     name='Farm',
    ...     type='warehouse',
    ...     storage_location=farm_storage_id,
    ...     input_location=farm_input_id,
    ...     output_location=farm_storage_id,
    ...     production_location=farm_production_id)
    >>> farm.save()

    >>> location1_id, location2_id = Location.create([{
    ...         'name': 'Location 1',
    ...         'code': 'L1',
    ...         'type': 'storage',
    ...         'parent': farm.storage_location.id,
    ...         }, {
    ...         'name': 'Location 2',
    ...         'code': 'L2',
    ...         'type': 'storage',
    ...         'parent': farm.storage_location.id,
    ...         }], config.context)
    >>> location1, location2 = (Location(location1_id), Location(location2_id))
    >>> silo1 = Location(
    ...     name='Silo 1',
    ...     code='S1',
    ...     type='storage',
    ...     parent=farm.storage_location,
    ...     silo=True,
    ...     locations_to_fed=[location1_id, location2_id])
    >>> silo1.save()

Create specie::

    >>> Specie = Model.get('farm.specie')
    >>> SpecieBreed = Model.get('farm.specie.breed')
    >>> SpecieFarmLine = Model.get('farm.specie.farm_line')
    >>> pigs_specie = Specie(
    ...     name='Pigs',
    ...     male_enabled=False,
    ...     female_enabled=False,
    ...     individual_enabled=True,
    ...     individual_product=individual_product,
    ...     group_enabled=True,
    ...     group_product=group_product,
    ...     prescription_enabled=True,
    ...     prescription_sequence=prescription_sequence,
    ...     removed_location=lost_found_location,
    ...     foster_location=lost_found_location,
    ...     lost_found_location=lost_found_location,
    ...     feed_lost_found_location=lost_found_location)
    >>> pigs_specie.save()
    >>> pigs_breed = SpecieBreed(
    ...     specie=pigs_specie,
    ...     name='Holland')
    >>> pigs_breed.save()
    >>> pigs_farm_line = SpecieFarmLine(
    ...     specie=pigs_specie,
    ...     event_order_sequence=event_order_sequence,
    ...     farm=farm,
    ...     has_individual=True,
    ...     individual_sequence=individual_sequence,
    ...     has_group=True,
    ...     group_sequence=group_sequence)
    >>> pigs_farm_line.save()

Create Feed product::

    >>> ProductUom = Model.get('product.uom')
    >>> kg, = ProductUom.find([('name', '=', 'Kilogram')])
    >>> gr, = ProductUom.find([('name', '=', 'Gram')])
    >>> feed_template = ProductTemplate(
    ...     name='Pig Feed',
    ...     default_uom=kg,
    ...     type='goods',
    ...     producible=True,
    ...     list_price=Decimal('40'),
    ...     cost_price=Decimal('25'))
    >>> feed_template.save()
    >>> feed_product = Product(template=feed_template)
    >>> feed_product.save()

Create Feed Components::

    >>> feed_component1_template = ProductTemplate(
    ...     name='Pig Feed Component 1',
    ...     default_uom=kg,
    ...     type='goods',
    ...     list_price=Decimal('30'),
    ...     cost_price=Decimal('20'))
    >>> feed_component1_template.save()
    >>> feed_component1 = Product(template=feed_component1_template)
    >>> feed_component1.save()

    >>> feed_component2_template = ProductTemplate(
    ...     name='Pig Feed Component 2',
    ...     default_uom=kg,
    ...     type='goods',
    ...     list_price=Decimal('50'),
    ...     cost_price=Decimal('30'))
    >>> feed_component2_template.save()
    >>> feed_component2 = Product(template=feed_component2_template)
    >>> feed_component2.save()

Create Bill of Material::

    >>> BOM = Model.get('production.bom')
    >>> BOMInput = Model.get('production.bom.input')
    >>> BOMOutput = Model.get('production.bom.output')
    >>> bom = BOM(name='Pig Feed')
    >>> input1 = BOMInput()
    >>> bom.inputs.append(input1)
    >>> input1.product = feed_component1
    >>> input1.quantity = 0.85
    >>> input2 = BOMInput()
    >>> bom.inputs.append(input2)
    >>> input2.product = feed_component2
    >>> input2.quantity = 150
    >>> input2.uom = gr
    >>> output = BOMOutput()
    >>> bom.outputs.append(output)
    >>> output.product = feed_product
    >>> output.quantity = 1
    >>> bom.save()

    >>> ProductBom = Model.get('product.product-production.bom')
    >>> feed_product.boms.append(ProductBom(bom=bom))
    >>> feed_product.save()

Create Drug product::

    >>> drug_template = ProductTemplate(
    ...     name='Drug additive',
    ...     default_uom=gr,
    ...     type='goods',
    ...     prescription_required=True,
    ...     list_price=Decimal('15'),
    ...     cost_price=Decimal('10'))
    >>> drug_template.save()
    >>> drug_product = Product(template=drug_template)
    >>> drug_product.save()

Create veterinarian::

    >>> Party = Model.get('party.party')
    >>> veterinarian = Party(
    ...     name='Veterinarian',
    ...     veterinarian=True,
    ...     collegiate_number='123456789')
    >>> veterinarian.save()

Create an Inventory::

    >>> Inventory = Model.get('stock.inventory')
    >>> InventoryLine = Model.get('stock.inventory.line')
    >>> inventory = Inventory()
    >>> inventory.location = warehouse.storage_location
    >>> inventory_line1 = InventoryLine()
    >>> inventory.lines.append(inventory_line1)
    >>> inventory_line1.product = feed_component1
    >>> inventory_line1.quantity = 300
    >>> inventory_line2 = InventoryLine()
    >>> inventory.lines.append(inventory_line2)
    >>> inventory_line2.product = feed_component2
    >>> inventory_line2.quantity = 5
    >>> inventory.save()
    >>> Inventory.confirm([inventory.id], config.context)
    >>> inventory.state
    'done'

Create three individuals in location L1::

    >>> Animal = Model.get('farm.animal')
    >>> individuals = [Animal(), Animal(), Animal()]
    >>> for individual in individuals:
    ...     individual.type = 'individual'
    ...     individual.specie = pigs_specie
    ...     individual.breed = pigs_breed
    ...     individual.arrival_date = now.date()
    ...     individual.initial_location = location1
    ...     individual.save()

Create group G1 with 4 units in location L2::

    >>> AnimalGroup = Model.get('farm.animal.group')
    >>> animal_group = AnimalGroup(
    ...     specie=pigs_specie,
    ...     breed=pigs_breed,
    ...     arrival_date=now.date(),
    ...     initial_location=location2,
    ...     initial_quantity=4)
    >>> animal_group.save()

Create a supply request of 100 Kg of feed for individuals in location L1 and
100 Kg of feed with prescription for grop in location L2::

    >>> SupplyRequest = Model.get('stock.supply_request')
    >>> SupplyRequestLine = Model.get('stock.supply_request.line')
    >>> supply_request = SupplyRequest(
    ...     company=company,
    ...     from_warehouse=warehouse,
    ...     to_warehouse=farm,
    ...     lines=[])
    >>> line1 = supply_request.lines.new()
    >>> line1.product = feed_product
    >>> line1.quantity = 100
    >>> line1.to_location = location1
    >>> line2 = supply_request.lines.new()
    >>> line2.product = feed_product
    >>> line2.quantity = 100
    >>> line2.to_location = location2
    >>> supply_request.save()

Confirm supply request and check that moves, productions and prescriptions has
been created::

    >>> supply_request.click('confirm')
    >>> supply_request.state
    'confirmed'
    >>> for line in supply_request.lines:
    ...     line.quantity == line.move.quantity == line.production.quantity
    True
    True
