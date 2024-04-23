# Generated by Django 5.0.4 on 2024-04-19 09:11

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0004_rename_model_switch_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="switch",
            name="aos_version",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="switch",
            name="fpga_version",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="switch",
            name="hardware_revision",
            field=models.CharField(default=1, max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="switch",
            name="part_number",
            field=models.CharField(default=1, max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="switch",
            name="serial_number",
            field=models.CharField(default=1, max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="switch",
            name="uboot_version",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]