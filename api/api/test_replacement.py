"""
Restore onto replacement Switches (#46): the port map and the config rewritten for the
replacement, against config lines as AOS 8 writes them in vcboot.cfg (ranges especially).
"""
from django.test import SimpleTestCase

from .replacement import ReplacementError, port_map, rank, rewrite_config, rewrite_links

# A vcboot.cfg as AOS 8.10 writes one, with ports in each section a member configures
VCBOOT = '''! File: /flash/working/vcboot.cfg (06/11/2026 10:12:44)
!
! Chassis:
system name "OS6860"
session prompt default "OS6860"
! Interface:
interfaces 1/1/1-4 speed 10000
interfaces 1/1/2 alias "to 1/1/7 of leaf2"
interfaces 1/1/7 admin-state disable
! Link Aggregate:
linkagg lacp agg 5 size 4 admin-state enable
linkagg lacp agg 5 actor admin-key 5
linkagg lacp port 1/1/1-2 actor admin-key 5
linkagg static port 1/1/3 agg 6
! VLAN:
vlan 10 admin-state enable
vlan 10 members port 1/1/5-6 untagged
vlan 20 members port 1/1/1-1/1/3 tagged
vlan 20 members linkagg 5 tagged
! IP:
ip interface "int10" address 10.1.1.1 mask 255.255.255.0 vlan 10
ip static-route 10.0.0.0/8 gateway 10.69.144.129 metric 1
! QOS:
qos port 1/1/2 trusted default classification tos
qos port 1/1/49A-49D maximum egress-bandwidth 100M
qos apply
! SPB/VXLAN/EVPN services:
service access port 1/1/6 vlan-xlation enable
service 1001 sap port 1/1/6:100
service 1002 sap port 1/1/6:all
! Port Mirroring:
port-mirroring 1 source port 1/1/2 bidirectional
port-mirroring 1 destination port 1/1/8
'''


def everything(port):
    return True


class RewriteConfigTest(SimpleTestCase):
    """The Link ports 1/1/1 and 1/1/2 move to 1/1/3 and 1/1/4 of the replacement."""

    def rewritten(self, ports=None, exists=everything, text=VCBOOT, new_model='OS6860'):
        return rewrite_config(text, ports if ports is not None else {'1/1/1': '1/1/3', '1/1/2': '1/1/4'}, exists,
                              'OS6860', new_model)

    def lines(self, **kwargs):
        return self.rewritten(**kwargs).text.splitlines()

    def test_the_same_ports_on_the_same_model_leave_the_config_as_it_is(self):
        self.assertEqual(rewrite_config(VCBOOT, {'1/1/1': '1/1/1'}, everything, 'OS6860', 'OS6860').text, VCBOOT)

    def test_a_link_port_is_rewritten_everywhere_it_is_named(self):
        lines = self.lines()
        self.assertIn('qos port 1/1/4 trusted default classification tos', lines)
        self.assertIn('port-mirroring 1 source port 1/1/4 bidirectional', lines)
        self.assertIn('linkagg lacp port 1/1/3-4 actor admin-key 5', lines)  # LACP: a range onto a range

    def test_a_range_whose_ports_no_longer_follow_each_other_becomes_a_line_per_run(self):
        # 1/1/1-4: 1/1/1-2 go to 1/1/3-4, and 1/1/3-4 of the old config are taken by them
        rewritten = self.rewritten()
        self.assertIn('interfaces 1/1/3-4 speed 10000', rewritten.text.splitlines())
        self.assertIn('Taken over by a Link\'s port, so left out of its config: 1/1/3-4.', rewritten.notes)
        ports = self.rewritten(ports={'1/1/1': '1/1/8', '1/1/2': '1/1/5'}, exists=lambda p: p != '1/1/6')
        self.assertIn('interfaces 1/1/3-5 speed 10000', ports.text.splitlines())
        self.assertIn('interfaces 1/1/8 speed 10000', ports.text.splitlines())

    def test_the_full_form_of_a_range_is_rewritten(self):
        lines = self.lines(ports={'1/1/1': '1/1/10', '1/1/2': '1/1/11'})
        self.assertIn('vlan 20 members port 1/1/3 tagged', lines)
        self.assertIn('vlan 20 members port 1/1/10-11 tagged', lines)

    def test_vlan_membership_of_a_port_the_replacement_lacks_is_left_out(self):
        rewritten = self.rewritten(exists=lambda port: port not in ('1/1/5', '1/1/6'))
        lines = rewritten.text.splitlines()
        self.assertNotIn('vlan 10 members port 1/1/5-6 untagged', lines)
        self.assertIn('vlan 10 admin-state enable', lines)
        self.assertNotIn('service 1001 sap port 1/1/6:100', lines)
        self.assertIn('Not on the replacement, so left out of its config: 1/1/5-6.', rewritten.notes)
        self.assertTrue(any(note.startswith('5 lines left out: ') and 'vlan 10 members port 1/1/5-6 untagged' in note
                            for note in rewritten.notes), rewritten.notes)

    def test_evpn_and_spb_access_lines_keep_their_sap(self):
        lines = self.lines(ports={'1/1/6': '1/1/1'})
        self.assertIn('service access port 1/1/1 vlan-xlation enable', lines)
        self.assertIn('service 1001 sap port 1/1/1:100', lines)
        self.assertIn('service 1002 sap port 1/1/1:all', lines)

    def test_split_ports_and_their_ranges(self):
        lines = self.lines(ports={'1/1/49B': '1/1/1'})
        self.assertIn('qos port 1/1/1 maximum egress-bandwidth 100M', lines)
        self.assertIn('qos port 1/1/49A maximum egress-bandwidth 100M', lines)
        self.assertIn('qos port 1/1/49C maximum egress-bandwidth 100M', lines)

    def test_quotes_comments_addresses_and_ids_are_left_alone(self):
        lines = self.lines()
        self.assertIn('interfaces 1/1/4 alias "to 1/1/7 of leaf2"', lines)
        self.assertEqual(lines[0], '! File: /flash/working/vcboot.cfg (06/11/2026 10:12:44)')
        self.assertIn('ip static-route 10.0.0.0/8 gateway 10.69.144.129 metric 1', lines)
        self.assertIn('vlan 20 members linkagg 5 tagged', lines)

    def test_the_system_name_and_prompt_follow_the_model(self):
        lines = self.lines(new_model='OS6860E-24')
        self.assertIn('system name "OS6860E-24"', lines)
        self.assertIn('session prompt default "OS6860E-24"', lines)
        mine = rewrite_config('system name "leaf1"\n', {}, everything, 'OS6860', 'OS6900-X48C6').text
        self.assertEqual(mine, 'system name "leaf1"\n')  # a name of the member's own is theirs

    def test_no_vcsetup_stays_none(self):
        self.assertIsNone(rewrite_config(None, {}, everything).text)


class PortMapTest(SimpleTestCase):

    def test_the_links_ports_in_port_order_onto_the_cabled_ports_in_port_order(self):
        self.assertEqual(port_map(['1/1/10', '1/1/2'], ['1/1/9', '1/1/5', '1/1/20']),
                         {'1/1/2': '1/1/5', '1/1/10': '1/1/9'})

    def test_too_few_cabled_ports(self):
        with self.assertRaisesRegex(ReplacementError, '1 cabled port free, and the Snapshot\'s Links need 2'):
            port_map(['1/1/1', '1/1/2'], ['1/1/1'])

    def test_links_move_to_the_replacement(self):
        links = [[{'switch': 1, 'port': '1/1/1'}, {'switch': 2, 'port': '1/1/1'}],
                 [{'switch': 2, 'port': '1/1/2'}, {'switch': 9, 'port': '1/1'}]]
        self.assertEqual(rewrite_links(links, {2: (7, {'1/1/1': '1/1/5', '1/1/2': '1/1/6'})}),
                         [[{'switch': 1, 'port': '1/1/1'}, {'switch': 7, 'port': '1/1/5'}],
                          [{'switch': 7, 'port': '1/1/6'}, {'switch': 9, 'port': '1/1'}]])

    def test_rank(self):
        self.assertEqual(rank('OS6900-X48C6', 'OS6900-X48C6'), 'same_model')
        self.assertEqual(rank('OS6900-X48C6', 'OS6900-T48C6'), 'same_family')
        self.assertEqual(rank('OS6900-X48C6', 'OS6360-P24'), 'other')


# Lines of a real vcboot.cfg, a VC of two OS6900 read on 10.69.144.131 (2026-11-03), and of
# 10.69.144.138 (OS6900-X24C2): anonymised (names, aliases), as AOS wrote them, trailing spaces kept
REAL_VCBOOT = (
    '! Chassis:\n'
    'system name "dc-1"\n'
    '! Interface:\n'
    'interfaces port 1/1/4 alias "uplink-4"\n'
    'interfaces port 1/1/5 alias "uplink-5"\n'
    'interfaces port 1/1/6 admin-state disable\n'
    'interfaces port 1/1/56A alias "VFL-Link"\n'
    'interfaces port 2/1/3 admin-state disable\n'
    'interfaces ddm enable\n'
    '! Link Aggregate:\n'
    'linkagg static agg 1 size 2 admin-state enable \n'
    'linkagg static port 1/1/1 agg 1\n'
    'linkagg static port 1/1/2 agg 2\n'
    'linkagg lacp port 1/1/7 actor admin-key 7777\n'
    'linkagg lacp port 1/1/25 actor admin-key 1\n'
    '! VLAN:\n'
    'vlan 900 members port 2/1/25 untagged\n'
    '! Port Mirroring:\n'
    'port-monitoring 1 source port 2/1/49A  file  /flash/pmonitor.enc  size 1  timeout 0 bidirectional capture-type brief disable\n'
    'sflow receiver 1 name probe-1 address 10.0.5.10 udp-port 6344 packet-size 1400 version 5 timeout 0\n'
    'sflow sampler 1 port 1/1/1-48 receiver 1 rate 128 sample-hdr-size 128\n'
    'sflow sampler 1 port 2/1/1-48 receiver 1 rate 128 sample-hdr-size 128\n'
    '! SVCMGR:\n'
    'service access port 1/1/4 vlan-xlation enable \n'
    'service access port 1/1/5 vlan-xlation enable \n'
    'service 9 sap linkagg 1:9\n'
    'service 201 sap port 1/1/5:0\n'
    'service 2192 sap port 1/1/4:0\n'
)


class RealConfigTest(SimpleTestCase):
    """The VC's Links on 1/1/4 and 1/1/5 move to 1/1/1 and 1/1/2 of a lone 28-port Switch."""

    def setUp(self):
        self.rewritten = rewrite_config(REAL_VCBOOT, {'1/1/4': '1/1/1', '1/1/5': '1/1/2'},
                                        lambda port: port in {f'1/1/{n}' for n in range(1, 29)}, 'OS6900-X72', 'OS6860N-U28')
        self.lines = self.rewritten.text.splitlines()

    def test_spb_access_ports_and_saps_follow_the_links(self):
        self.assertIn('service access port 1/1/1 vlan-xlation enable ', self.lines)
        self.assertIn('service access port 1/1/2 vlan-xlation enable ', self.lines)
        self.assertIn('service 201 sap port 1/1/2:0', self.lines)
        self.assertIn('service 2192 sap port 1/1/1:0', self.lines)
        self.assertIn('service 9 sap linkagg 1:9', self.lines)
        self.assertIn('interfaces port 1/1/1 alias "uplink-4"', self.lines)

    def test_a_range_is_cut_to_the_ports_the_replacement_has(self):
        self.assertIn('sflow sampler 1 port 1/1/1-3 receiver 1 rate 128 sample-hdr-size 128', self.lines)
        self.assertIn('sflow sampler 1 port 1/1/6-28 receiver 1 rate 128 sample-hdr-size 128', self.lines)
        self.assertFalse(any('2/1/' in line for line in self.lines), self.lines)  # no second chassis

    def test_lacp_and_static_aggregates(self):
        self.assertIn('linkagg lacp port 1/1/7 actor admin-key 7777', self.lines)
        self.assertIn('linkagg lacp port 1/1/25 actor admin-key 1', self.lines)
        self.assertNotIn('linkagg static port 1/1/1 agg 1', self.lines)  # 1/1/1 now carries a Link
        self.assertIn('linkagg static agg 1 size 2 admin-state enable ', self.lines)

    def test_what_is_left_out_is_said(self):
        self.assertNotIn('interfaces port 1/1/56A alias "VFL-Link"', self.lines)
        self.assertNotIn('port-monitoring 1 source port 2/1/49A', self.rewritten.text)
        self.assertNotIn('vlan 900 members port 2/1/25 untagged', self.lines)
        self.assertIn("Taken over by a Link's port, so left out of its config: 1/1/1-2.", self.rewritten.notes)
        dropped = next(n for n in self.rewritten.notes if n.startswith('Not on the replacement'))
        for port in ('1/1/29-48', '1/1/56A', '2/1/1-48', '2/1/49A'):  # 2/1/25 among them
            self.assertIn(port, dropped)
