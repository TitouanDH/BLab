from django.db import migrations, models


class Migration(migrations.Migration):
    # Only the choices change: Django emits no SQL for it (sqlmigrate shows a no-op), so main's
    # code keeps working on the shared database (docs/adr/0002)
    shared_db_safe = True

    dependencies = [
        ('api', '0004_release_cleanup_quarantine'),
    ]

    operations = [
        migrations.AlterField(
            model_name='switchevent',
            name='kind',
            field=models.CharField(choices=[('inspection', 'Inspection'), ('release', 'Release'), ('cleanup', 'Cleanup'), ('quarantine', 'Quarantine'), ('quarantine_lifted', 'Quarantine lifted'), ('out_of_service', 'Out of service'), ('back_in_service', 'Back in service')], max_length=32),
        ),
    ]
