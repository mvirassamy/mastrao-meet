from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0053_native_asr_dispatch_reservation")]

    operations = [
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="asr_dispatch_queue",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="asr_retry_streak",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
