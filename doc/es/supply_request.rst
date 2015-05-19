#:after:stock_supply_request/supply_request:paragraph:confirm#

En caso que el producto a producir tenga la marca |prescription_required| y
tenga definida una |prescription_template|, esta se utilizará esta para crear
la receta y se associará a la reserva y a la producción.

.. |prescription_required| field:: product.template/prescription_required
.. |prescription_template| field:: product.product/prescription_template
