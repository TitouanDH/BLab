"""What each port can run at, from show interfaces capability (#82)."""
from django.test import SimpleTestCase

from .port_capability import Capability, parse_capabilities


class PortCapabilityTest(SimpleTestCase):
    # Rows as 10.69.144.130 (an OS6900 Backbone) and 10.69.144.152 (an OS6860N-P24M) print them, 2026-10-21
    BACKBONE = """ Ch/Slot/Port   AutoNeg       Pause       Crossover     Speed    Duplex
--------------+--------+----------------+-----------+----------+----------
 1/2/9   CAP      DIS   Tx/Rx/Tx&Rx/DIS           -        10G       Full
 1/2/9   DEF      DIS               DIS           -        10G       Full
 1/3/1   CAP   EN/DIS   Tx/Rx/Tx&Rx/DIS  MDI/X/Auto  10/100/1G  Full/Half
 1/3/1   DEF       EN               DIS           -       Auto       Auto
 1/8/7   CAP      DIS   Tx/Rx/Tx&Rx/DIS           -     10G40G       Full
 1/8/7   DEF      DIS               DIS           -        40G       Full
"""
    SWITCH = """                                                                                    Macsec      Macsec
 Ch/Slot/Port   AutoNeg       Pause       Crossover        Speed         Duplex     Supported   256-bit
--------------+--------+----------------+-----------+------------------+----------+-----------+--------------
 1/1/1    CAP   EN/DIS   Tx/Rx/Tx&Rx/DIS  MDI/X/Auto  100/1G/2.5G/5G/10G  Full/Half   YES         YES
 1/1/1    DEF       EN               DIS           -               Auto       Auto    -           -
 1/2/1A   CAP      DIS   Tx/Rx/Tx&Rx/DIS           -        1G/10G/25G       Full   NO          NO
"""

    def test_it_reads_the_speeds_and_medium_of_each_port(self):
        self.assertEqual(parse_capabilities(self.BACKBONE), {
            '1/2/9': Capability(frozenset({10000}), False, '10G'),
            '1/3/1': Capability(frozenset({10, 100, 1000}), True, '10/100/1G'),
            '1/8/7': Capability(frozenset({10000, 40000}), False, '10G40G'),
        })
        switch = parse_capabilities(self.SWITCH)
        self.assertEqual(switch['1/1/1'], Capability(frozenset({100, 1000, 2500, 5000, 10000}), True, '100/1G/2.5G/5G/10G'))
        self.assertEqual(switch['1/2/1A'], Capability(frozenset({1000, 10000, 25000}), False, '1G/10G/25G'))

    def test_ports_share_a_speed_or_not(self):
        backbone, switch = parse_capabilities(self.BACKBONE), parse_capabilities(self.SWITCH)
        self.assertTrue(switch['1/1/1'].runs_with(backbone['1/2/9']))
        self.assertTrue(switch['1/2/1A'].runs_with(backbone['1/3/1']))
        self.assertFalse(backbone['1/3/1'].runs_with(backbone['1/8/7']))

    def test_nothing_readable_is_nothing(self):
        self.assertEqual(parse_capabilities('ERROR: Invalid entry: "capability"'), {})
