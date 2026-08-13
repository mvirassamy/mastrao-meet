# Generated for Mastrao's opt-in host handoff boundary.

import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0023_mastrao_room_binding")]

    operations = [
        migrations.CreateModel(
            name="MastraoHostIdentity",
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
                ("host_ref", models.CharField(max_length=160, unique=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mastrao_host_identity",
                        to="core.user",
                    ),
                ),
            ],
            options={"db_table": "meet_mastrao_host_identity"},
        ),
        migrations.CreateModel(
            name="MastraoHostGrant",
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
                ("handoff_ref", models.CharField(max_length=160, unique=True)),
                ("grant_ref", models.CharField(max_length=160, unique=True)),
                ("grant_digest", models.CharField(max_length=64)),
                ("credential_digest", models.CharField(max_length=64)),
                ("meeting_ref", models.CharField(max_length=160)),
                ("room_ref", models.CharField(max_length=100)),
                ("provider_binding_digest", models.CharField(max_length=64)),
                ("platform_session_ref", models.CharField(max_length=160)),
                ("session_nonce_digest", models.CharField(max_length=64)),
                ("issued_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField()),
                (
                    "identity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="grants",
                        to="core.mastraohostidentity",
                    ),
                ),
                (
                    "room_binding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="host_grants",
                        to="core.mastraoroombinding",
                    ),
                ),
            ],
            options={"db_table": "meet_mastrao_host_grant"},
        ),
        migrations.AddConstraint(
            model_name="mastraohostgrant",
            constraint=models.CheckConstraint(
                condition=models.Q(grant_digest__regex=r"^[a-f0-9]{64}$"),
                name="mastrao_host_grant_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraohostgrant",
            constraint=models.CheckConstraint(
                condition=models.Q(credential_digest__regex=r"^[a-f0-9]{64}$"),
                name="mastrao_host_credential_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraohostgrant",
            constraint=models.CheckConstraint(
                condition=models.Q(session_nonce_digest__regex=r"^[a-f0-9]{64}$"),
                name="mastrao_host_session_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraohostgrant",
            constraint=models.CheckConstraint(
                condition=models.Q(provider_binding_digest__regex=r"^[a-f0-9]{64}$"),
                name="mastrao_host_provider_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraohostgrant",
            constraint=models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("issued_at")),
                name="mastrao_host_grant_positive_lifetime",
            ),
        ),
    ]
