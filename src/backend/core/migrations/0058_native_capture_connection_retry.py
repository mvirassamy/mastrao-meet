from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0057_observability_metrics_history_indexes")]

    operations = [
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="provider_start_attempts",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="superseded_provider_job_refs",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
