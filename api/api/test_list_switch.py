"""
list_switch (#37): every Switch says how many cabled ports it has, its Ports paired with a
UNI, so that the Reservation page can tell how many Links a Switch can carry. Everyone gets
the count, also for a Switch they may not work on.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Port, Reservation
from .tests import make_switches


class CabledPortsTest(TestCase):

    def setUp(self):
        self.alice = User.objects.create_user('alice', email='alice@example.com', password='pw')
        self.carol = User.objects.create_user('carol', email='carol@example.com', password='pw')
        self.two, self.linked, self.none = make_switches(3)
        for i in (1, 2):
            Port.objects.create(switch=self.two, port_switch=f'1/1/{i}', backbone='10.0.0.100', port_backbone=f'1/1/{i}')
        # A Port in a Link is still a cabled port
        Port.objects.create(switch=self.linked, port_switch='1/1/1', backbone='10.0.0.100', port_backbone='1/1/9', svlan=1001)
        Reservation.objects.create(switch=self.linked, user=self.carol, end_date=timezone.now() + timezone.timedelta(days=3))

    def list_switch(self, user):
        client = APIClient()
        client.force_authenticate(user)
        response = client.get('/api/list_switch/')
        self.assertEqual(response.status_code, 200, response.data)
        return {s['id']: s for s in response.data['switchs']}

    def test_every_switch_has_its_number_of_cabled_ports(self):
        switches = self.list_switch(self.alice)
        self.assertEqual(switches[self.two.id]['cabled_ports'], 2)
        self.assertEqual(switches[self.linked.id]['cabled_ports'], 1)
        self.assertEqual(switches[self.none.id]['cabled_ports'], 0)

    def test_the_count_and_the_details_come_for_a_switch_one_may_not_work_on(self):
        linked = self.list_switch(self.alice)[self.linked.id]
        self.assertEqual(linked['mngt_IP'], self.linked.mngt_IP)
        self.assertEqual(linked['cabled_ports'], 1)

    def test_counting_them_takes_no_query_per_switch(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries():
            with CaptureQueriesContext(connection) as captured:
                self.list_switch(self.alice)
            return len(captured)

        before = queries()
        for switch in make_switches(3):
            Port.objects.create(switch=switch, port_switch='1/1/1', backbone='10.0.0.100', port_backbone=f'1/2/{switch.id}')
        self.assertEqual(queries(), before)
