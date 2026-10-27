import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0058_native_capture_connection_retry")]

    operations = [
        migrations.CreateModel(
            name="MastraoRoomAudioTrackEffect",
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
                ("audio_run_ref", models.CharField(max_length=200)),
                ("pipeline", models.CharField(max_length=8)),
                ("track_ref", models.CharField(max_length=200)),
                ("authorization_generation", models.PositiveBigIntegerField()),
                ("operation", models.CharField(max_length=48)),
                ("authorization_digest", models.CharField(max_length=64)),
                ("effect_jti", models.CharField(max_length=200, unique=True)),
                ("effect_digest", models.CharField(max_length=64)),
                ("compact_effect", models.TextField(max_length=16384)),
                ("destination_identity", models.CharField(max_length=80)),
                ("expires_at", models.DateTimeField()),
                (
                    "state",
                    models.CharField(
                        choices=[("pending", "Pending"), ("delivered", "Delivered")],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                (
                    "room_binding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="room_audio_track_effects",
                        to="core.mastraoroombinding",
                    ),
                ),
            ],
            options={"db_table": "meet_mastrao_room_audio_track_effect"},
        ),
        migrations.AddConstraint(
            model_name="mastraoroomaudiotrackeffect",
            constraint=models.UniqueConstraint(
                fields=(
                    "room_binding",
                    "audio_run_ref",
                    "pipeline",
                    "track_ref",
                    "authorization_generation",
                ),
                name="mastrao_audio_track_generation_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraoroomaudiotrackeffect",
            constraint=models.CheckConstraint(
                condition=models.Q(authorization_digest__regex="^[a-f0-9]{64}$"),
                name="mastrao_audio_track_auth_digest",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraoroomaudiotrackeffect",
            constraint=models.CheckConstraint(
                condition=models.Q(effect_digest__regex="^[a-f0-9]{64}$"),
                name="mastrao_audio_track_effect_digest",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraoroomaudiotrackeffect",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(delivered_at__isnull=True, state="pending")
                    | models.Q(delivered_at__isnull=False, state="delivered")
                ),
                name="mastrao_audio_track_delivery_shape",
            ),
        ),
        migrations.AddIndex(
            model_name="mastraoroomaudiotrackeffect",
            index=models.Index(
                fields=["state", "expires_at"],
                name="mastrao_audio_pending_idx",
            ),
        ),
    ]
