import subprocess

from django.core.management.base import BaseCommand, CommandError
from django.db.migrations.loader import MigrationLoader

from api.migration_safety import unsafe_operations


class Command(BaseCommand):
    help = (
        "Fail if a migration not yet on main could break main's code on the database "
        "shared by production and pre-prod (api/migration_safety.py)."
    )

    def add_arguments(self, parser):
        parser.add_argument('--base', default='origin/main', help='git ref production runs')

    def handle(self, *args, base, **options):
        on_base = subprocess.run(
            ['git', 'ls-tree', '--name-only', f'{base}:api/api/migrations/'],
            capture_output=True, text=True, check=True,
        ).stdout.split()

        loader = MigrationLoader(None, ignore_no_migrations=True)
        problems = []
        for (app, name), migration in sorted(loader.disk_migrations.items()):
            if app != 'api' or f'{name}.py' in on_base:
                continue
            problems += [f'api.{name}: {reason}' for reason in unsafe_operations(migration)]

        if problems:
            raise CommandError(
                'Migrations unsafe for the shared production database:\n  '
                + '\n  '.join(problems)
                + '\nSplit the change into steps, or set shared_db_safe = True with a comment '
                'explaining why main keeps working (docs/adr/0002).'
            )
        self.stdout.write(f'No unsafe migrations compared to {base}.')
