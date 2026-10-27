"""Migration proof for room-and-pipeline worker uniqueness."""

from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

import pytest

from core import models
from core.factories import RoomFactory, UserFactory


@pytest.mark.django_db(transaction=True)
def test_room_pipeline_migration_stops_before_ambiguous_legacy_rows():
    """Never discard or silently select one of two historical run effects."""
    executor = MigrationExecutor(connection)
    current_core_migrations = executor.loader.graph.leaf_nodes("core")
    previous = [("core", "0061_mastrao_room_audio_source_outbox")]
    current = [("core", "0062_room_audio_room_pipeline_unique")]
    executor.migrate(previous)
    old_apps = executor.loader.project_state(previous).apps
    old_run_effect = old_apps.get_model("core", "MastraoRoomAudioRunEffect")
    binding = models.MastraoRoomBinding.objects.create(
        effect_key="room_audio_migration_fixture",
        arguments_digest="a" * 64,
        meeting_ref="meeting_audio_migration_fixture",
        room_ref="room_audio_migration_fixture",
        owner_ref="owner_audio_migration_fixture",
        provider_binding_digest="b" * 64,
        room=RoomFactory(name="room_audio_migration_fixture"),
        owner=UserFactory(),
    )
    common = {
        "room_binding_id": binding.pk,
        "pipeline": "post",
        "effect_digest": "c" * 64,
        "compact_effect": "header.payload.signature",
        "destination_identity": f"room-audio-worker-post-{'d' * 32}",
        "expires_at": timezone.now() + timedelta(minutes=5),
    }
    first = old_run_effect.objects.create(
        **common,
        audio_run_ref="audio_run_migration_first",
        effect_jti="effect_migration_first",
    )
    second = old_run_effect.objects.create(
        **common,
        audio_run_ref="audio_run_migration_second",
        effect_jti="effect_migration_second",
    )

    try:
        with pytest.raises(RuntimeError, match="explicit reconciliation"):
            MigrationExecutor(connection).migrate(current)

        second.delete()
        MigrationExecutor(connection).migrate(current)
        assert models.MastraoRoomAudioRunEffect.objects.filter(pk=first.pk).exists()
    finally:
        MigrationExecutor(connection).migrate(current_core_migrations)
