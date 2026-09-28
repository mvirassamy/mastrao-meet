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


def test_canary_reconciler_dispatches_only_native_work_on_the_native_pool():
    """Recover missed native wake-ups without opening the legacy pipeline."""

    document = yaml.safe_load(
        (
            STAGING / "reconcile-native-transcription-synthetic-canary.cronjob.yaml"
        ).read_text()
    )
    assert document["kind"] == "CronJob"
    assert document["metadata"]["name"] == "reconcile-native-transcription"
    assert document["spec"]["schedule"] == "* * * * *"
    assert document["spec"]["concurrencyPolicy"] == "Forbid"

    pod = document["spec"]["jobTemplate"]["spec"]["template"]
    assert pod["metadata"]["labels"] == {
        "app.kubernetes.io/name": "worker-native",
        "mastrao.com/component": "worker-native",
        "mastrao.com/role": "native-reconciler",
    }
    spec = pod["spec"]
    assert spec["serviceAccountName"] == "worker-native"
    assert spec["nodeSelector"]["mastrao.com/node-pool"] == "capture"
    [container] = spec["containers"]
    assert container["command"] == [
        "/app/.venv/bin/python",
        "manage.py",
        "reconcile_mastrao_recordings",
        "--native-only",
        "--limit",
        "2",
    ]
    environment = {item["name"]: item.get("value") for item in container["env"]}
    assert environment["MASTRAO_MEETING_TRANSCRIPTION_ENABLED"] == "False"
    assert environment["MASTRAO_NATIVE_SOURCE_TRANSFER_ENABLED"] == "True"
    assert environment["MASTRAO_NATIVE_ASR_ENABLED"] == "True"
