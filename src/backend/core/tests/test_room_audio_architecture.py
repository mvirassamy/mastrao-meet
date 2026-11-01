"""Guard the canonical Room Audio transcription architecture."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[4]
ACTIVE_SOURCE_ROOTS = (
    REPOSITORY_ROOT / "src/backend/core",
    REPOSITORY_ROOT / "src/backend/meet",
    REPOSITORY_ROOT / "src/helm/meet",
)
FORBIDDEN_MARKERS = (
    "ROOM_AUDIO_ASR_ENABLED",
    "ROOM_AUDIO_SOURCE_TRANSFER_ENABLED",
    "mastrao-transcription",
    "mastrao-native-asr-ready",
    "mastrao-native-asr-retry",
    "MastraoTranscriptionBinding",
    "MastraoTranscriptionEffect",
    "MastraoTranscriptionProviderAttempt",
    "transcriptions/transcribe",
    "ROOM_SUBTITLE_ENABLED",
    "RoomSubtitleControl",
    "RoomSubtitleAdvisoryKey",
    "start-subtitle",
    "stop-subtitle",
    "subtitle-state",
    "agent-subtitles",
    "multi_user_transcriber",
    "live_transcriber",
)
REMOVED_MODULES = (
    "mastrao_transcription_adapter.py",
    "mastrao_transcription_artifact.py",
    "mastrao_transcription_attempt.py",
    "mastrao_transcription_contract.py",
    "mastrao_transcription_pipeline.py",
    "mastrao_transcription_worker.py",
    "tasks/transcription.py",
    "services/subtitle.py",
    "services/subtitle_control.py",
    "services/subtitle_lock.py",
    "services/subtitle_reconciliation.py",
    "tasks/subtitle.py",
)


def _active_source_files():
    for source_root in ACTIVE_SOURCE_ROOTS:
        for path in source_root.rglob("*"):
            if not path.is_file():
                continue
            if "migrations" in path.parts or "tests" in path.parts:
                continue
            if path.suffix not in {".py", ".tpl", ".yaml", ".yml"}:
                continue
            yield path


def test_legacy_transcription_modules_are_removed():
    """Keep deleted participant-transcription modules out of the runtime."""

    core_root = REPOSITORY_ROOT / "src/backend/core"

    for relative_path in REMOVED_MODULES:
        assert not (core_root / relative_path).exists()


def test_room_audio_is_the_only_active_transcription_runtime():
    """Reject active runtime references to the superseded architecture."""

    violations = []

    for path in _active_source_files():
        content = path.read_text(encoding="utf-8")
        markers = [marker for marker in FORBIDDEN_MARKERS if marker in content]
        if markers:
            violations.append((path.relative_to(REPOSITORY_ROOT), markers))

    assert not violations
