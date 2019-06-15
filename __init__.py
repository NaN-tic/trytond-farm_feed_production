#The COPYRIGHT file at the top level of this repository contains the full
#copyright notices and license terms.

from trytond.pool import Pool
from . import feed_production


def register():
    Pool.register(
        feed_production.SupplyRequestLine,
        feed_production.Production,
        feed_production.Prescription,
        module='farm_feed_production', type_='model')
