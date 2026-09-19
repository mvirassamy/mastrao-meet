"""Frozen epoch delivery evidence, never a new participant authority."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0046_native_asr_delivery")]
    operations = [
        migrations.AddField(
            model_name="mastraortctrackepoch",
            name="native_participant_label",
            field=models.JSONField(null=True, blank=True),
        ),
    ]
