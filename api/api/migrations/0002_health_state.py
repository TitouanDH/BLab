import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='switch',
            name='platform',
            field=models.CharField(choices=[('AOS6', 'AOS6'), ('AOS9', 'AOS9'), ('AOSX', 'AOSX'), ('UNKNOWN', 'Unknown')], default='UNKNOWN', max_length=16),
        ),
        migrations.AddField(
            model_name='switch',
            name='software_version',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AddField(
            model_name='switch',
            name='baseline_version',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AddField(
            model_name='switch',
            name='health_state',
            field=models.CharField(choices=[('UNKNOWN', 'Unknown'), ('HEALTHY', 'Healthy'), ('RESERVED', 'Reserved'), ('DIRTY', 'Dirty'), ('CLEANUP_PENDING', 'Cleanup pending'), ('CLEANING', 'Cleaning'), ('UNREACHABLE', 'Unreachable'), ('QUARANTINED', 'Quarantined'), ('STALE', 'Stale')], default='UNKNOWN', max_length=32),
        ),
        migrations.AddField(
            model_name='switch',
            name='last_health_check',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='switch',
            name='last_verified_clean',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='switch',
            name='health_summary',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='switch',
            name='quarantined_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='switch',
            name='quarantine_reason',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.CreateModel(
            name='HealthCheck',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('RUNNING', 'Running'), ('HEALTHY', 'Healthy'), ('DIRTY', 'Dirty'), ('UNREACHABLE', 'Unreachable'), ('ERROR', 'Error')], default='RUNNING', max_length=16)),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('adapter', models.CharField(blank=True, default='', max_length=32)),
                ('evidence', models.JSONField(blank=True, default=dict)),
                ('error_message', models.TextField(blank=True, default='')),
                ('requested_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='requested_health_checks', to=settings.AUTH_USER_MODEL)),
                ('switch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='health_checks', to='api.switch')),
            ],
        ),
        migrations.CreateModel(
            name='HealthFinding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('category', models.CharField(choices=[('PORT_LINK', 'Unexpected port or link'), ('CONFIGURATION', 'Configuration drift'), ('SERVICE', 'Unexpected service'), ('STACK', 'Stack or virtual chassis'), ('ACCOUNT', 'Account or password'), ('REACHABILITY', 'Reachability'), ('UNAUTHORIZED_USE', 'Unauthorized use'), ('CLEANUP', 'Cleanup failure')], max_length=32)),
                ('severity', models.CharField(choices=[('INFO', 'Info'), ('WARNING', 'Warning'), ('CRITICAL', 'Critical')], default='WARNING', max_length=16)),
                ('code', models.CharField(max_length=64)),
                ('message', models.TextField()),
                ('resource', models.CharField(blank=True, default='', max_length=255)),
                ('observed', models.JSONField(blank=True, default=dict)),
                ('expected', models.JSONField(blank=True, default=dict)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('health_check', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='findings', to='api.healthcheck')),
                ('switch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='health_findings', to='api.switch')),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
