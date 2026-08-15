# Generated manually for the forward-only Mastrao recording boundary.

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0027_mastrao_room_closing_fence")]

    operations = [
        migrations.CreateModel(
            name="MastraoRecordingBinding",
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
                ("recording_ref", models.CharField(max_length=160, unique=True)),
                ("provider_binding_digest", models.CharField(max_length=64)),
                ("policy_ref", models.CharField(max_length=160)),
                ("notice_version", models.CharField(max_length=160)),
                ("notice_digest", models.CharField(max_length=64)),
                (
                    "purpose",
                    models.CharField(default="meeting_recording", max_length=64),
                ),
                (
                    "scope",
                    models.CharField(
                        default="room_composite_audio_video_screen", max_length=80
                    ),
                ),
                ("retention_expires_at", models.DateTimeField()),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("prepared", "Prepared"),
                            ("starting", "Starting"),
                            ("active", "Active"),
                            ("stopping", "Stopping"),
                            ("processing", "Processing"),
                            ("finalized", "Finalized"),
                            ("cancelled", "Cancelled"),
                            ("failed", "Failed"),
                        ],
                        default="prepared",
                        max_length=20,
                    ),
                ),
                (
                    "provider_recording_ref",
                    models.CharField(
                        blank=True, max_length=160, null=True, unique=True
                    ),
                ),
                (
                    "artifact_ref",
                    models.CharField(
                        blank=True, max_length=160, null=True, unique=True
                    ),
                ),
                (
                    "storage_binding_digest",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                (
                    "object_ref",
                    models.CharField(blank=True, max_length=1024, null=True),
                ),
                (
                    "content_type",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                ("byte_size", models.PositiveBigIntegerField(blank=True, null=True)),
                (
                    "checksum_algorithm",
                    models.CharField(blank=True, max_length=20, null=True),
                ),
                (
                    "checksum_digest",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                (
                    "provider_version_digest",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                ("region_ref", models.CharField(blank=True, max_length=160, null=True)),
                (
                    "encryption_ref",
                    models.CharField(blank=True, max_length=160, null=True),
                ),
                (
                    "lifecycle_policy_ref",
                    models.CharField(blank=True, max_length=160, null=True),
                ),
                ("artifact_verified_at", models.DateTimeField(blank=True, null=True)),
                ("artifact_receipt_claims", models.JSONField(blank=True, default=dict)),
                (
                    "artifact_receipt_digest",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                (
                    "recording",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mastrao_binding",
                        to="core.recording",
                    ),
                ),
                (
                    "room_binding",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="recording_binding",
                        to="core.mastraoroombinding",
                    ),
                ),
            ],
            options={"db_table": "meet_mastrao_recording_binding"},
        ),
        migrations.CreateModel(
            name="MastraoRecordingDecision",
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
                ("decision_request_id", models.CharField(max_length=160, unique=True)),
                (
                    "participant_kind",
                    models.CharField(
                        choices=[("host", "Host"), ("guest", "Guest")], max_length=8
                    ),
                ),
                ("participant_ref", models.CharField(max_length=160)),
                ("participant_session_digest", models.CharField(max_length=64)),
                ("participant_grant_digest", models.CharField(max_length=64)),
                (
                    "decision",
                    models.CharField(
                        choices=[
                            ("accepted", "Accepted"),
                            ("refused", "Refused"),
                            ("withdrawn", "Withdrawn"),
                        ],
                        max_length=12,
                    ),
                ),
                ("assertion_jti", models.CharField(max_length=200, unique=True)),
                ("assertion_digest", models.CharField(max_length=64)),
                ("semantic_digest", models.CharField(max_length=64)),
                ("decided_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "core_state_version",
                    models.PositiveBigIntegerField(blank=True, null=True),
                ),
                (
                    "recording_binding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="decisions",
                        to="core.mastraorecordingbinding",
                    ),
                ),
            ],
            options={
                "db_table": "meet_mastrao_recording_decision",
                "ordering": ("created_at",),
            },
        ),
        migrations.CreateModel(
            name="MastraoRecordingEffect",
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
                (
                    "operation",
                    models.CharField(
                        choices=[("start", "Start"), ("stop", "Stop")], max_length=8
                    ),
                ),
                ("arguments_digest", models.CharField(max_length=64)),
                ("effect_jti", models.CharField(max_length=200, unique=True)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("applied", "Applied"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=12,
                    ),
                ),
                (
                    "provider_observation",
                    models.CharField(blank=True, max_length=32, null=True),
                ),
                ("receipt_claims", models.JSONField(blank=True, default=dict)),
                (
                    "receipt_digest",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                (
                    "recording_binding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="effects",
                        to="core.mastraorecordingbinding",
                    ),
                ),
            ],
            options={"db_table": "meet_mastrao_recording_effect"},
        ),
        migrations.CreateModel(
            name="MastraoRecordingArtifactAccess",
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
                ("grant_jti", models.CharField(max_length=200, unique=True)),
                ("grant_digest", models.CharField(max_length=64, unique=True)),
                ("artifact_ref", models.CharField(max_length=160)),
                ("subject_external_id_digest", models.CharField(max_length=64)),
                ("platform_session_digest", models.CharField(max_length=64)),
                ("retry_cookie_digest", models.CharField(max_length=64)),
                ("expires_at", models.DateTimeField()),
                (
                    "consumed_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "recording_binding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="artifact_accesses",
                        to="core.mastraorecordingbinding",
                    ),
                ),
            ],
            options={"db_table": "meet_mastrao_recording_artifact_access"},
        ),
        migrations.AddConstraint(
            model_name="mastraorecordingbinding",
            constraint=models.CheckConstraint(
                condition=models.Q(provider_binding_digest__regex="^[a-f0-9]{64}$"),
                name="mastrao_recording_provider_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraorecordingbinding",
            constraint=models.CheckConstraint(
                condition=models.Q(notice_digest__regex="^[a-f0-9]{64}$"),
                name="mastrao_recording_notice_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraorecordingbinding",
            constraint=models.CheckConstraint(
                condition=models.Q(purpose="meeting_recording"),
                name="mastrao_recording_purpose_fixed",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraorecordingbinding",
            constraint=models.CheckConstraint(
                condition=models.Q(scope="room_composite_audio_video_screen"),
                name="mastrao_recording_scope_fixed",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraorecordingdecision",
            constraint=models.UniqueConstraint(
                fields=(
                    "recording_binding",
                    "participant_ref",
                    "participant_session_digest",
                    "decision",
                ),
                name="unique_mastrao_recording_session_decision",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraorecordingdecision",
            constraint=models.CheckConstraint(
                condition=models.Q(participant_session_digest__regex="^[a-f0-9]{64}$"),
                name="mastrao_recording_session_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraorecordingeffect",
            constraint=models.UniqueConstraint(
                fields=("recording_binding", "operation"),
                name="unique_mastrao_recording_operation",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraorecordingeffect",
            constraint=models.CheckConstraint(
                condition=models.Q(arguments_digest__regex="^[a-f0-9]{64}$"),
                name="mastrao_recording_effect_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraorecordingartifactaccess",
            constraint=models.CheckConstraint(
                condition=models.Q(grant_digest__regex="^[a-f0-9]{64}$"),
                name="mastrao_recording_access_grant_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraorecordingartifactaccess",
            constraint=models.CheckConstraint(
                condition=models.Q(platform_session_digest__regex="^[a-f0-9]{64}$"),
                name="mastrao_recording_access_session_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraorecordingartifactaccess",
            constraint=models.CheckConstraint(
                condition=models.Q(retry_cookie_digest__regex="^[a-f0-9]{64}$"),
                name="mastrao_recording_access_retry_digest_format",
            ),
        ),
    ]
