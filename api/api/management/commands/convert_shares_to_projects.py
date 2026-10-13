"""
Turns Topology sharing into Projects, once, at the maintenance window (docs/adr/0007): each user
holding a Reservation without a Project gets "<username>'s lab", their Reservations are made into
it, each user they shared their Topology with becomes a member, and their Topology layout is
copied over. Safe to run again: it only moves Reservations that have no Project yet.

    docker compose exec django python manage.py convert_shares_to_projects --dry-run
    docker compose exec django python manage.py convert_shares_to_projects
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from api import projects


class DryRun(Exception):
    pass


class Command(BaseCommand):
    help = "Turns Topology sharing into Projects (one per holder of Reservations); safe to run twice."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Say what it would do, and change nothing.')

    def handle(self, *args, dry_run=False, **options):
        try:
            with transaction.atomic():
                done = projects.convert_shares(log=self.stdout.write)
                if dry_run:
                    raise DryRun()
        except DryRun:
            pass
        verb = 'Would have' if dry_run else 'Done:'
        self.stdout.write(f"{verb} {done.projects_created} Project(s) created, {done.reservations_moved} "
                          f"Reservation(s) moved, {done.members_added} member(s) added from Shares, "
                          f"layout of {done.positions_copied} Switch(es) copied.")
        if not dry_run and not done.reservations_moved:
            self.stdout.write("Nothing left to convert.")
