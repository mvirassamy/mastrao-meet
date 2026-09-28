"""Persist the room subtitle agent lifecycle state."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the smallest reversible storage needed by subtitle lifecycle APIs."""

    dependencies = [("core", "0047_native_participant_label")]

    operations = [
        migrations.AddField(
            model_name="room",
            name="subtitle_state",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Durable lifecycle state for the room subtitle agent.",
            ),
        ),
    ]
