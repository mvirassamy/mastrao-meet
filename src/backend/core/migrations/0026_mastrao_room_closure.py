# Generated for Mastrao's irreversible canonical room lifecycle.
# pylint: disable=invalid-name,missing-class-docstring,missing-module-docstring

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0025_mastrao_guest_grant")]

    operations = [
        migrations.CreateModel(
            name="MastraoRoomClosure",
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
                ("organization_external_id", models.CharField(max_length=200)),
                ("meeting_ref", models.CharField(max_length=160)),
                ("room_ref", models.CharField(max_length=100)),
                ("provider_binding_digest", models.CharField(max_length=64)),
                ("close_ref", models.CharField(max_length=160, unique=True)),
                ("effect_key", models.CharField(max_length=160, unique=True)),
                ("arguments_digest", models.CharField(max_length=64)),
                (
                    "state",
                    models.CharField(
                        choices=[("pending", "Pending"), ("applied", "Applied")],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("requested_at", models.DateTimeField()),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                (
                    "provider_observation",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("deleted", "Deleted"),
                            ("already_absent", "Already absent"),
                        ],
                        max_length=24,
                        null=True,
                    ),
                ),
                ("receipt_claims", models.JSONField(blank=True, default=dict)),
                ("receipt_digest", models.CharField(blank=True, max_length=64, null=True)),
                (
                    "room_binding",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="closure",
                        to="core.mastraoroombinding",
                    ),
                ),
            ],
            options={"db_table": "meet_mastrao_room_closure"},
        ),
        migrations.AddConstraint(
            model_name="mastraoroomclosure",
            constraint=models.CheckConstraint(
                condition=models.Q(arguments_digest__regex=r"^[a-f0-9]{64}$"),
                name="mastrao_closure_arguments_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraoroomclosure",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    provider_binding_digest__regex=r"^[a-f0-9]{64}$"
                ),
                name="mastrao_closure_provider_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraoroomclosure",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        state="pending",
                        applied_at__isnull=True,
                        provider_observation__isnull=True,
                        receipt_digest__isnull=True,
                        receipt_claims={},
                    )
                    | models.Q(
                        state="applied",
                        applied_at__isnull=False,
                        provider_observation__isnull=False,
                        receipt_digest__isnull=False,
                    )
                ),
                name="mastrao_closure_state_shape",
            ),
        ),
    ]
