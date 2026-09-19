import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0044_native_admission_delivery")]
    operations = [
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="source_receipt",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="source_claim",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="source_claim_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="source_next_at",
            field=models.DateTimeField(
                db_index=True, default=django.utils.timezone.now
            ),
        ),
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="source_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="source_error",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
