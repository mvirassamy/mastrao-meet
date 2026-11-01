"""Remove the superseded recording-artifact transcription pipeline."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0065_remove_mastraonativecapturestart")]

    operations = [
        migrations.DeleteModel(name="MastraoTranscriptionProviderAttempt"),
        migrations.DeleteModel(name="MastraoTranscriptionEffect"),
        migrations.DeleteModel(name="MastraoTranscriptionBinding"),
    ]
