from django.contrib.auth.models import User
from django.db import migrations, models
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from . import fake_devices
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
