from django.db import migrations, models


def require_unique_room_pipelines(apps, _schema_editor):
    """Stop before DDL when legacy rows need an explicit operator decision."""
    run_effect = apps.get_model("core", "MastraoRoomAudioRunEffect")
    duplicate_exists = (
        run_effect.objects.values("room_binding_id", "pipeline")
        .annotate(row_count=models.Count("id"))
        .filter(row_count__gt=1)
        .exists()
    )
    if duplicate_exists:
        raise RuntimeError(
            "room-audio room/pipeline duplicates require explicit reconciliation"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0061_mastrao_room_audio_source_outbox"),
    ]

    operations = [
        migrations.RunPython(
            require_unique_room_pipelines,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="mastraoroomaudioruneffect",
            name="mastrao_audio_run_pipeline_unique",
        ),
        migrations.AddConstraint(
            model_name="mastraoroomaudioruneffect",
            constraint=models.UniqueConstraint(
                fields=("room_binding", "pipeline"),
                name="mastrao_audio_run_pipeline_unique",
            ),
        ),
    ]
