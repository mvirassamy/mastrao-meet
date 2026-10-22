"""Allow participant-free room closure in the RTC observation model state."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0058_host_grant_organization")]

    operations = [
        migrations.AlterField(
            model_name="mastraortcobservation",
            name="participant_sid",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AlterField(
            model_name="mastraortcobservation",
            name="rtc_identity",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
