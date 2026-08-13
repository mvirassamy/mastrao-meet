# Generated for Mastrao's opt-in canonical room adapter.

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0022_user_default_room_access_level_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="MastraoRoomBinding",
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
                ("effect_key", models.CharField(max_length=160, unique=True)),
                ("arguments_digest", models.CharField(max_length=64)),
                ("meeting_ref", models.CharField(max_length=160)),
                ("room_ref", models.CharField(max_length=100, unique=True)),
                ("owner_ref", models.CharField(max_length=160)),
                ("provider_binding_digest", models.CharField(max_length=64)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mastrao_room_bindings",
                        to="core.user",
                    ),
                ),
                (
                    "room",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mastrao_binding",
                        to="core.room",
                    ),
                ),
            ],
            options={"db_table": "meet_mastrao_room_binding"},
        ),
        migrations.AddConstraint(
            model_name="mastraoroombinding",
            constraint=models.CheckConstraint(
                condition=models.Q(arguments_digest__regex=r"^[a-f0-9]{64}$"),
                name="mastrao_room_arguments_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraoroombinding",
            constraint=models.CheckConstraint(
                condition=models.Q(provider_binding_digest__regex=r"^[a-f0-9]{64}$"),
                name="mastrao_room_provider_digest_format",
            ),
        ),
    ]
