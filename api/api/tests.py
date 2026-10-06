from django.contrib.auth.models import User
from django.db import migrations, models
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from . import fake_devices
from .backbone import APIRequestError, Backbone, Service, parse_service
from .migration_safety import unsafe_operations
from .models import Port, Reservation, Switch


@override_settings(BLAB_DEVICES='fake')
class LinkLifecycleWithFakeDevicesTest(TestCase):
    """Reserve two switches, link them, unlink them, release: all against the fake backbone."""

    def setUp(self):
        fake_devices.backbone.reset()
        self.user = User.objects.create_user('alice', password='pw')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.ports = []
        for i in (1, 2):
            switch = Switch.objects.create(
                mngt_IP=f'10.0.0.{i}', model='OS6860', console='TODO',
                part_number='pn', hardware_revision='A', serial_number=f'sn{i}',
            )
            self.ports.append(Port.objects.create(
                switch=switch, port_switch='1/1/1',
                backbone='10.0.0.100', port_backbone=f'1/1/{i}',
            ))

    def reserve_both(self):
        for port in self.ports:
            response = self.client.post('/api/reserve/', {'switch': port.switch.id}, format='json')
            self.assertEqual(response.status_code, 201, response.data)

    def test_connect_then_disconnect(self):
        self.reserve_both()
        a, b = self.ports

        response = self.client.post('/api/connect/', {'portA': a.id, 'portB': b.id}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertIsNotNone(a.svlan)
        self.assertEqual(a.svlan, b.svlan)
        self.assertIn(f'ethernet-service sap {a.svlan} uni port 1/1/2',
                      fake_devices.backbone.config['10.0.0.100'])

        response = self.client.post('/api/disconnect/', {'portA': a.id, 'portB': b.id}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        a.refresh_from_db()
        self.assertIsNone(a.svlan)
        self.assertEqual(fake_devices.backbone.config['10.0.0.100'], [])

    def test_release_tears_down_link(self):
        self.reserve_both()
        a, b = self.ports
        self.client.post('/api/connect/', {'portA': a.id, 'portB': b.id}, format='json')

        response = self.client.post('/api/release/', {'switch': a.switch.id}, format='json')
        self.assertIn(response.status_code, (200, 204), response.data)
        self.assertFalse(Reservation.objects.filter(switch=a.switch).exists())
        self.assertEqual(fake_devices.backbone.config['10.0.0.100'], [])


class BackboneServicesTest(SimpleTestCase):
    """Service operations against the fake backbone, and parsing of the real snapshot format."""

    IP = '10.0.0.100'

    def setUp(self):
        self.fake = fake_devices.FakeBackbone()
        self.backbone = Backbone(self.IP, self.fake.cli)

    def test_configure_then_read_service(self):
        self.backbone.configure_service(1001, 'blab_1001', ['1/1/1', '1/1/2'])
        self.assertEqual(
            self.backbone.read_service(1001),
            Service(svlan=1001, svlan_configured=True, name='blab_1001', sap=True,
                    unis=('1/1/1', '1/1/2'), cvlan_all=True),
        )
        self.assertIsNone(self.backbone.read_service(1002))

    def test_remove_uses_the_name_found_on_the_device(self):
        self.backbone.configure_service(1001, 'bob_1001', ['1/1/1', '1/1/2'])
        self.backbone.remove_service(1001)
        self.assertEqual(self.fake.config[self.IP], [])
        self.assertIn((self.IP, 'no ethernet-service service-name bob_1001 svlan 1001'), self.fake.commands)

    def test_remove_is_idempotent_and_handles_half_built_services(self):
        self.fake.fail_on('cvlan all')
        with self.assertRaises(APIRequestError):
            self.backbone.configure_service(1001, 'blab_1001', ['1/1/1'])
        self.assertFalse(self.backbone.read_service(1001).is_complete('blab_1001', ['1/1/1']))

        self.backbone.remove_service(1001)
        self.assertEqual(self.fake.config[self.IP], [])
        sent = len(self.fake.commands)
        self.backbone.remove_service(1001)
        self.assertEqual(len(self.fake.commands), sent + 1)  # only the read

    def test_remove_leaves_other_svlans_alone(self):
        self.backbone.configure_service(1001, 'a', ['1/1/1'])
        self.backbone.configure_service(10010, 'b', ['1/1/2'])
        self.backbone.remove_service(1001)
        self.assertEqual(self.backbone.read_service(10010).unis, ('1/1/2',))

    def test_fail_after_lets_n_commands_through_then_fails_once(self):
        self.fake.fail_after(2)
        self.fake.cli(self.IP, 'ethernet-service svlan 1 admin-state enable')
        self.fake.cli(self.IP, 'ethernet-service svlan 2 admin-state enable')
        with self.assertRaises(APIRequestError):
            self.fake.cli(self.IP, 'ethernet-service svlan 3 admin-state enable')
        self.fake.cli(self.IP, 'ethernet-service svlan 4 admin-state enable')
        self.assertEqual(len(self.fake.config[self.IP]), 3)

    def test_fail_on_can_target_one_backbone(self):
        self.fake.fail_on('svlan', ip='10.0.0.200')
        self.fake.cli(self.IP, 'ethernet-service svlan 1 admin-state enable')
        with self.assertRaises(APIRequestError):
            self.fake.cli('10.0.0.200', 'ethernet-service svlan 1 admin-state enable')

    def test_parse_real_snapshot_with_quotes_and_port_ranges(self):
        snapshot = """
! VLAN SVLAN:
ethernet-service svlan 1001 admin-state enable
ethernet-service svlan 1002 admin-state enable
ethernet-service service-name "alice_1001" svlan 1001
ethernet-service sap 1001 service-name "alice_1001"
ethernet-service sap 1001 uni port 1/1/3-5
ethernet-service sap 1001 cvlan all
ethernet-service sap 10011 uni port 1/1/9
"""
        self.assertEqual(
            parse_service(snapshot, 1001),
            Service(svlan=1001, svlan_configured=True, name='alice_1001', sap=True,
                    unis=('1/1/3', '1/1/4', '1/1/5'), cvlan_all=True),
        )
        self.assertEqual(parse_service(snapshot, 1002), Service(svlan=1002, svlan_configured=True))
        self.assertIsNone(parse_service(snapshot, 101))


class MigrationSafetyForSharedDatabaseTest(SimpleTestCase):
    """Migrations not on main yet must keep main's code working (docs/adr/0002)."""

    def migration(self, *operations, **attrs):
        migration = migrations.Migration('0099_test', 'api')
        migration.operations = list(operations)
        for key, value in attrs.items():
            setattr(migration, key, value)
        return migration

    def test_additive_changes_are_safe(self):
        migration = self.migration(
            migrations.CreateModel('Thing', [('id', models.BigAutoField(primary_key=True))]),
            migrations.AddField('port', 'note', models.CharField(max_length=10, null=True)),
            migrations.AddField('port', 'flag', models.BooleanField(db_default=False)),
            migrations.AddIndex('port', models.Index(fields=['status'], name='port_status_idx')),
        )
        self.assertEqual(unsafe_operations(migration), [])

    def test_not_null_column_without_db_default_is_unsafe(self):
        migration = self.migration(
            migrations.AddField('port', 'flag', models.BooleanField(default=False)),
        )
        self.assertEqual(len(unsafe_operations(migration)), 1)

    def test_destructive_changes_are_unsafe(self):
        migration = self.migration(
            migrations.RemoveField('port', 'svlan'),
            migrations.RenameField('port', 'status', 'state'),
            migrations.DeleteModel('TopologyShare'),
        )
        self.assertEqual(len(unsafe_operations(migration)), 3)

    def test_reviewed_migration_can_opt_out(self):
        migration = self.migration(migrations.RemoveField('port', 'svlan'), shared_db_safe=True)
        self.assertEqual(unsafe_operations(migration), [])
