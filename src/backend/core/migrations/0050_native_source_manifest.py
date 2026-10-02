from django.db import migrations, models


def require_empty_native_capture_table(apps, _schema_editor):
    """Refuse a one-way pipeline cutover while legacy captures still exist."""
    capture = apps.get_model("core", "MastraoNativeCaptureStart")
    if capture.objects.exists():
        raise RuntimeError(
            "Native TrackEgress cutover requires an empty "
            "MastraoNativeCaptureStart table"
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0049_subtitle_advisory_key")]
    operations = [
        migrations.RunPython(
            require_empty_native_capture_table,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="mastraonativecapturestart",
            name="output_prefix",
        ),
        migrations.AddField(
            model_name="mastraonativecapturestart",
            name="source_manifest",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
