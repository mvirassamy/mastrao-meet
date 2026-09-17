"""Send-once native intent, retained independently of the admission flag."""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0041_mastraortcconnection_mastraortctrackepoch_and_more")]

    operations = [
        migrations.CreateModel(
            name="MastraoNativeCaptureStart",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        help_text="primary key for the record as UUID",
                        primary_key=True,
                        serialize=False,
                        verbose_name="id",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        help_text="date and time at which a record was created",
                        verbose_name="created on",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        help_text="date and time at which a record was last updated",
                        verbose_name="updated on",
                    ),
                ),
                ("capture_ref", models.UUIDField(unique=True)),
                ("effect_key", models.CharField(max_length=160, unique=True)),
                ("arguments_digest", models.CharField(max_length=64)),
                ("organization_external_id", models.CharField(max_length=160)),
                ("output_prefix", models.CharField(max_length=512)),
                (
                    "provider_job_ref",
                    models.CharField(
                        max_length=128, unique=True, null=True, blank=True
                    ),
                ),
                ("receipt_claims", models.JSONField(default=dict, blank=True)),
                (
                    "epoch",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="core.mastraortctrackepoch",
                    ),
                ),
            ],
            options={
                "db_table": "meet_mastrao_native_capture_start",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(arguments_digest__regex=r"^[a-f0-9]{64}$"),
                        name="mastrao_native_start_digest",
                    )
                ],
            },
        ),
    ]
