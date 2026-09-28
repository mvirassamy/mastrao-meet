"""Regression checks for the staging native transcription canary patches."""

from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
STAGING = REPOSITORY_ROOT / "deploy" / "kubernetes" / "staging"


def _environment(filename, container_name):
    document = yaml.safe_load((STAGING / filename).read_text())
    containers = document["spec"]["template"]["spec"]["containers"]
    [container] = [item for item in containers if item["name"] == container_name]
    return {item["name"]: item["value"] for item in container["env"]}


def test_canary_uses_only_the_native_transcription_pipeline():
    """Keep deployable canaries away from the incompatible legacy ASR path."""

    api = _environment("meet-api-transcription-synthetic-canary.patch.yaml", "api")
    worker = _environment(
        "worker-native-transcription-synthetic-canary.patch.yaml", "worker-native"
    )
    gateway = _environment(
        "asr-gateway-transcription-synthetic-canary.patch.yaml", "asr-gateway"
    )

    assert api == {
        "MASTRAO_MEETING_RECORDING_START_ENABLED": "True",
        "MASTRAO_MEETING_TRANSCRIPTION_ENABLED": "False",
    }
    assert worker == {
        "MASTRAO_MEETING_TRANSCRIPTION_ENABLED": "False",
        "MASTRAO_NATIVE_SOURCE_TRANSFER_ENABLED": "True",
        "MASTRAO_NATIVE_ASR_ENABLED": "True",
    }
    assert gateway == {"ASR_GATEWAY_NATIVE_TEST_DATA_ONLY": "true"}
