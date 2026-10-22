"""Retain the exact subtitle generation eligible for terminal delivery."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0059_rtc_room_finished_observation")]

    operations = [
        migrations.AddField(
            model_name="roomsubtitlecontrol",
            name="draining_generation",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
    ]
