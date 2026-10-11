from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0052_mutable_video_decisions")]

    operations = [
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="asr_dispatch_ref",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="asr_dispatch_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
