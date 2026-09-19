from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_transcription_provider_attempt_rate_limited"),
    ]

    operations = [
        migrations.AddField(
            model_name="mastraoguestgrant",
            name="display_name",
            field=models.CharField(blank=True, max_length=160, null=True),
        ),
    ]
