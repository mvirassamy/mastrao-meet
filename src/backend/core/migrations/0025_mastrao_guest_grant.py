# Generated for Mastrao's opt-in guest invitation boundary.
# pylint: disable=invalid-name,missing-class-docstring,missing-module-docstring

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0024_mastrao_host_handoff")]

    operations = [
        migrations.CreateModel(
            name="MastraoGuestGrant",
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
                ("grant_ref", models.CharField(max_length=160, unique=True)),
                ("redemption_id", models.CharField(max_length=160, unique=True)),
                ("invitation_ref", models.CharField(max_length=160)),
                ("guest_ref", models.CharField(max_length=160, unique=True)),
                ("organization_external_id", models.CharField(max_length=160)),
                ("grant_digest", models.CharField(max_length=64)),
                ("credential_digest", models.CharField(max_length=64)),
                ("meeting_ref", models.CharField(max_length=160)),
                ("room_ref", models.CharField(max_length=100)),
                ("provider_binding_digest", models.CharField(max_length=64)),
                ("session_nonce_digest", models.CharField(max_length=64)),
                ("issued_at", models.DateTimeField()),
                (
                    "consumed_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("expires_at", models.DateTimeField()),
                (
                    "admission_state",
                    models.CharField(
                        choices=[
                            ("waiting", "Waiting"),
                            ("allowed", "Allowed"),
                            ("denied", "Denied"),
                        ],
                        default="waiting",
                        max_length=16,
                    ),
                ),
                (
                    "decision_ref",
                    models.CharField(
                        blank=True, max_length=160, null=True, unique=True
                    ),
                ),
                ("decision_allow", models.BooleanField(blank=True, null=True)),
                (
                    "decision_grant_digest",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                (
                    "decision_receipt_digest",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                (
                    "decision_confirmed_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "room_binding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="guest_grants",
                        to="core.mastraoroombinding",
                    ),
                ),
            ],
            options={"db_table": "meet_mastrao_guest_grant"},
        ),
        migrations.AddConstraint(
            model_name="mastraoguestgrant",
            constraint=models.CheckConstraint(
                condition=models.Q(grant_digest__regex=r"^[a-f0-9]{64}$"),
                name="mastrao_guest_grant_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraoguestgrant",
            constraint=models.CheckConstraint(
                condition=models.Q(credential_digest__regex=r"^[a-f0-9]{64}$"),
                name="mastrao_guest_credential_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraoguestgrant",
            constraint=models.CheckConstraint(
                condition=models.Q(session_nonce_digest__regex=r"^[a-f0-9]{64}$"),
                name="mastrao_guest_session_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraoguestgrant",
            constraint=models.CheckConstraint(
                condition=models.Q(provider_binding_digest__regex=r"^[a-f0-9]{64}$"),
                name="mastrao_guest_provider_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraoguestgrant",
            constraint=models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("issued_at")),
                name="mastrao_guest_grant_positive_lifetime",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraoguestgrant",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        decision_ref__isnull=True,
                        decision_allow__isnull=True,
                        decision_grant_digest__isnull=True,
                        decision_receipt_digest__isnull=True,
                        decision_confirmed_at__isnull=True,
                        admission_state="waiting",
                    )
                    | models.Q(
                        decision_ref__isnull=False,
                        decision_allow__isnull=False,
                    )
                ),
                name="mastrao_guest_decision_shape",
            ),
        ),
    ]
