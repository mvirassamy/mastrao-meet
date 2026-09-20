from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
QUALIFIED_EGRESS_IMAGE = (
    "livekit/egress:v1.13.0@"
    "sha256:980ff439431df2c773573721ab6da19e15bdc1f049ab7cb80e87470bf174c12f"
)


class LiveKitEgressContractTest(unittest.TestCase):
    def test_compose_uses_qualified_room_composite_image(self):
        compose = (ROOT / "compose.yml").read_text()

        self.assertIn(f"image: {QUALIFIED_EGRESS_IMAGE}", compose)
        self.assertNotIn("livekit/egress:v1.14.1", compose)

    def test_removed_sdk_source_switch_stays_absent(self):
        config = (ROOT / "docker/livekit/config/livekit-egress.yaml").read_text()

        self.assertNotIn("enable_room_composite_sdk_source", config)


if __name__ == "__main__":
    unittest.main()
