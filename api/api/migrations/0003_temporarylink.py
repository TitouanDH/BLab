import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0002_health_state'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TemporaryLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('svlan', models.PositiveIntegerField()),
                ('backbone', models.CharField(max_length=255)),
                ('service_name', models.CharField(max_length=255)),
                ('state', models.CharField(choices=[('REQUESTED', 'Requested'), ('ACTIVE', 'Active'), ('DISCONNECTING', 'Disconnecting'), ('DISCONNECTED', 'Disconnected'), ('FAILED', 'Failed')], default='REQUESTED', max_length=20)),
                ('backbone_verified_at', models.DateTimeField(blank=True, null=True)),
                ('disconnected_at', models.DateTimeField(blank=True, null=True)),
                ('failure_reason', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='created_temporary_links', to=settings.AUTH_USER_MODEL)),
                ('port_a', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='temporary_links_a', to='api.port')),
                ('port_b', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='temporary_links_b', to='api.port')),
                ('reservation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='temporary_links', to='api.reservation')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddConstraint(
            model_name='temporarylink',
            constraint=models.UniqueConstraint(fields=('port_a', 'port_b', 'svlan'), name='unique_temporary_link_instance'),
        ),
    ]
