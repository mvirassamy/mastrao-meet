"""Remove the superseded participant-track subtitle control pipeline."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0066_remove_legacy_transcription_pipeline")]

    operations = [
        migrations.DeleteModel(name="RoomSubtitleControl"),
        migrations.DeleteModel(name="RoomSubtitleAdvisoryKey"),
    ]
