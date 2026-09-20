import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0045_native_audio_transfer")]
    operations = [
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="asr_receipt",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="asr_acknowledged",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="asr_claim",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="asr_claim_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="asr_next_at",
            field=models.DateTimeField(
                db_index=True, default=django.utils.timezone.now
            ),
        ),
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="asr_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="asr_error",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
