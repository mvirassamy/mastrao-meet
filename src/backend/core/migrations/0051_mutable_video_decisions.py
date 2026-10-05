"""Allow distinct decision requests to repeat yes after a changed response."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0050_native_source_manifest")]

    operations = [
        migrations.RemoveConstraint(
            model_name="mastraorecordingdecision",
            name="unique_mastrao_recording_session_decision",
        ),
    ]
