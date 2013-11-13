#The COPYRIGHT file at the top level of this repository contains the full
#copyright notices and license terms.

from trytond.pool import Pool
from .feed_production import *


def register():
    Pool.register(
        SupplyRequestLine,
        Production,
        Prescription,
        module='farm_feed_production', type_='model')
