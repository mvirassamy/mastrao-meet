"""Allow distinct decision requests to repeat yes after a changed response."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0051_normal_post_meeting_transcription")]

    operations = [
        migrations.RemoveConstraint(
            model_name="mastraorecordingdecision",
            name="unique_mastrao_recording_session_decision",
        ),
    ]
