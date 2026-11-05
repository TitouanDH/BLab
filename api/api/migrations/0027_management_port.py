# Contribute a Switch without an EMP (#81): its Management port, a front port ('': its EMP).
# Additive, with a database default, as production's older code shares the database (ADR 0002).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0026_switchaccount_read_only'),
    ]

    operations = [
        migrations.AddField(
            model_name='switch',
            name='management_port',
            field=models.CharField(blank=True, db_default='', default='', max_length=32),
        ),
        migrations.AddField(
            model_name='switchcontribution',
            name='management_port',
            field=models.CharField(blank=True, db_default='', default='', max_length=32),
        ),
    ]
