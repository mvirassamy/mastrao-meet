import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0062_room_audio_room_pipeline_unique")]

    operations = [
        migrations.AddField(
            model_name="mastraoroomaudiosourceoutbox",
            name="asr_acknowledged",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="mastraoroomaudiosourceoutbox",
            name="asr_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="mastraoroomaudiosourceoutbox",
            name="asr_next_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name="mastraoroomaudiosourceoutbox",
            name="asr_claim",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraoroomaudiosourceoutbox",
            name="asr_claim_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraoroomaudiosourceoutbox",
            name="asr_receipt",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraoroomaudiosourceoutbox",
            name="asr_error",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddConstraint(
            model_name="mastraoroomaudiosourceoutbox",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(asr_claim__isnull=True, asr_claim_until__isnull=True)
                    | models.Q(
                        asr_claim__isnull=False,
                        asr_claim_until__isnull=False,
                    )
                ),
                name="mastrao_room_audio_asr_claim_shape",
            ),
        ),
        migrations.AddIndex(
            model_name="mastraoroomaudiosourceoutbox",
            index=models.Index(
                fields=["state", "asr_acknowledged", "asr_next_at"],
                name="mastrao_room_asr_pending_idx",
            ),
        ),
    ]
