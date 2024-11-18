# Generated by Django 5.0.4 on 2024-11-18 10:07

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0005_switch_aos_version_switch_fpga_version_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="switch",
            name="aos_version",
        ),
        migrations.RemoveField(
            model_name="switch",
            name="fpga_version",
        ),
        migrations.RemoveField(
            model_name="switch",
            name="uboot_version",
        ),
        migrations.AddField(
            model_name="port",
            name="status",
            field=models.CharField(
                blank=True,
                choices=[("UP", "Up"), ("DOWN", "Down")],
                default="DOWN",
                max_length=10,
                null=True,
            ),
        ),
    ]
