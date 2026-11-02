import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_ROOTS = (ROOT / "deploy", ROOT / "scripts")
SOURCE_SUFFIXES = {".json", ".mjs", ".py", ".sh", ".yaml", ".yml"}
FORBIDDEN_MARKERS = (
    "MASTRAO_NATIVE_CAPTURE_START_ENABLED",
    "MASTRAO_NATIVE_PREENTRY_ENABLED",
    "MASTRAO_NATIVE_SOURCE_TRANSFER_ENABLED",
    "MASTRAO_NATIVE_ASR_ENABLED",
    "worker-native",
    "worker-native-asr-ready",
    "worker-native-asr-retry",
    "reconcile-native-transcription",
)


class RoomAudioArchitectureTests(unittest.TestCase):
    def test_active_delivery_code_cannot_restore_participant_transcription(self):
        violations = []
        for root in ACTIVE_ROOTS:
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                    continue
                content = path.read_text(encoding="utf-8")
                for marker in FORBIDDEN_MARKERS:
                    if marker in content:
                        violations.append(f"{path.relative_to(ROOT)}: {marker}")

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
