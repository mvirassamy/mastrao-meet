import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0063_room_audio_asr_delivery")]

    operations = [
        migrations.AddField(
            model_name="mastraortctrackepoch",
            name="room_audio_run_refs",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="mastraortctrackepoch",
            name="room_audio_admitted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraortctrackepoch",
            name="room_audio_admission_blocked",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="mastraortctrackepoch",
            name="room_audio_admission_next_at",
            field=models.DateTimeField(
                db_index=True, default=django.utils.timezone.now
            ),
        ),
        migrations.AddField(
            model_name="mastraortctrackepoch",
            name="room_audio_admission_claim",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraortctrackepoch",
            name="room_audio_admission_claim_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraortctrackepoch",
            name="room_audio_participant_label",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.RemoveField(
            model_name="mastraortctrackepoch",
            name="native_admission_capture_ref",
        ),
        migrations.RemoveField(
            model_name="mastraortctrackepoch",
            name="native_admission_blocked",
        ),
        migrations.RemoveField(
            model_name="mastraortctrackepoch",
            name="native_admission_next_at",
        ),
        migrations.RemoveField(
            model_name="mastraortctrackepoch",
            name="native_admission_claim",
        ),
        migrations.RemoveField(
            model_name="mastraortctrackepoch",
            name="native_admission_claim_until",
        ),
        migrations.RemoveField(
            model_name="mastraortctrackepoch",
            name="native_participant_label",
        ),
    ]
