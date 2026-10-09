"""
The Reservation list (#32): each row carries the holder's username and whether the caller
may work on it (Renew, Release), so the Reservation page needs no lookup per Reservation;
Lab status rows carry the holder's id, so a page can tell the caller's own Switches.
"""
from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext

from .models import TopologyShare
from .test_release_cleanup import CleanupTestCase, make_switch


class ListReservationTest(CleanupTestCase):
    def setUp(self):
        super().setUp()
        self.carol = User.objects.create_user('carol', password='pw')
        self.other = make_switch(2)
        self.third = make_switch(3)
        self.reserve(self.alice)
        self.reserve(self.bob, self.other)
        self.reserve(self.carol, self.third)
        TopologyShare.objects.create(owner=self.bob, target=self.alice)

    def rows(self, user):
        response = self.client_for(user).get('/api/list_reservation/')
        self.assertEqual(response.status_code, 200)
        return {row['switch']: row for row in response.data}

    def test_each_row_names_its_holder(self):
        rows = self.rows(self.alice)
        self.assertEqual({switch: row['username'] for switch, row in rows.items()},
                         {self.switch.id: 'alice', self.other.id: 'bob', self.third.id: 'carol'})

    def test_may_work_follows_the_topology_rule(self):
        rows = self.rows(self.alice)
        # Her own, and Bob's (he shares his Topology with her), not Carol's
        self.assertEqual({switch: row['may_work'] for switch, row in rows.items()},
                         {self.switch.id: True, self.other.id: True, self.third.id: False})
        self.assertEqual({switch: row['may_work'] for switch, row in self.rows(self.bob).items()},
                         {self.switch.id: False, self.other.id: True, self.third.id: False})

    def test_the_rows_keep_their_fields(self):
        row = self.rows(self.alice)[self.switch.id]
        for field in ('id', 'switch', 'user', 'creation_date', 'end_date', 'renewals', 'renewals_left',
                      'admin_exception'):
            self.assertIn(field, row)
        self.assertEqual(row['user'], self.alice.id)

    def test_the_query_count_does_not_grow_with_the_reservations(self):
        client = self.client_for(self.alice)
        with CaptureQueriesContext(connection) as few:
            client.get('/api/list_reservation/')
        for n in range(4, 9):
            self.reserve(User.objects.create_user(f'user{n}', password='pw'), make_switch(n))
        with CaptureQueriesContext(connection) as many:
            client.get('/api/list_reservation/')
        self.assertEqual(len(many), len(few))


class LabStatusHolderTest(CleanupTestCase):
    def test_each_row_carries_its_holder_id(self):
        self.reserve(self.bob)
        make_switch(2)
        rows = self.client_for(self.alice).get('/api/lab_status/').data['switches']
        self.assertEqual([(row['holder'], row['holder_id']) for row in rows], [('bob', self.bob.id), (None, None)])
