import uuid

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0048_roomsubtitlecontrol"),
    ]

    operations = [
        migrations.AddField(
            model_name="roomsubtitlecontrol",
            name="room_finished_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="RoomSubtitleAdvisoryKey",
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
                (
                    "room_sid",
                    models.CharField(
                        max_length=128,
                        unique=True,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="room_sid must be a LiveKit room SID.",
                                regex="^RM_[A-Za-z0-9_-]{1,124}$",
                            )
                        ],
                    ),
                ),
                ("lock_key", models.PositiveIntegerField(unique=True)),
            ],
            options={"db_table": "meet_room_subtitle_advisory_key"},
        ),
        migrations.RunSQL(
            sql=(
                "CREATE SEQUENCE IF NOT EXISTS "
                "meet_room_subtitle_advisory_key_seq "
                "AS integer START WITH 1 MINVALUE 1 "
                "MAXVALUE 2147483647 NO CYCLE"
            ),
            reverse_sql="DROP SEQUENCE IF EXISTS meet_room_subtitle_advisory_key_seq",
        ),
    ]
