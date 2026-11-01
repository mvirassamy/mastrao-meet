from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0064_room_audio_track_admission")]

    operations = [
        migrations.DeleteModel(name="MastraoNativeCaptureStart"),
    ]
