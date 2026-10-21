"""Retain verified tenant provenance without inferring old grants' organization."""

# pylint: disable=invalid-name

from django.db import migrations, models


class Migration(migrations.Migration):
    """Nullable receipt field with no inferred backfill or runtime activation."""

    dependencies = [("core", "0057_observability_metrics_history_indexes")]

    operations = [
        migrations.AddField(
            model_name="mastraohostgrant",
            name="organization_external_id",
            field=models.CharField(blank=True, max_length=160, null=True),
        ),
    ]
