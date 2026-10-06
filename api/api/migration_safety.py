"""Pre-prod runs dev's code against production's database, and migrates it first.

So every migration that is not on main yet must keep main's code working: add tables,
nullable columns, columns with a database default, indexes. Anything else has to be split
into steps (stop using it on main first, remove it later). See
docs/adr/0002-preprod-shares-production-database.md.

A migration that has been checked by hand can opt out with `shared_db_safe = True` on its
Migration class, next to a comment explaining why.
"""
from django.db import migrations
from django.db.models import NOT_PROVIDED

# Operations that old code cannot survive, or that can't be judged automatically
_UNSAFE = {
    migrations.DeleteModel: 'drops a table main still uses',
    migrations.RemoveField: 'drops a column main still uses',
    migrations.RenameModel: 'renames a table main still uses',
    migrations.RenameField: 'renames a column main still uses',
    migrations.AlterField: 'changes a column main still uses',
    migrations.AlterModelTable: 'renames a table main still uses',
    migrations.AddConstraint: 'may reject rows main still writes',
    migrations.RunSQL: 'raw SQL cannot be checked',
    migrations.RunPython: 'Python code cannot be checked',
}


def unsafe_operations(migration):
    """Reasons why `migration` could break main's code on the shared database."""
    if getattr(migration, 'shared_db_safe', False):
        return []
    reasons = []
    for operation in migration.operations:
        name = f'{type(operation).__name__} {getattr(operation, "name", "")}'.strip()
        for unsafe_type, why in _UNSAFE.items():
            if isinstance(operation, unsafe_type):
                reasons.append(f'{name}: {why}')
        if isinstance(operation, migrations.AddField):
            field = operation.field
            if not (field.null or field.many_to_many or field.db_default is not NOT_PROVIDED):
                reasons.append(
                    f'AddField {operation.model_name}.{operation.name}: NOT NULL without '
                    'db_default, so main\'s inserts would fail'
                )
    return reasons
