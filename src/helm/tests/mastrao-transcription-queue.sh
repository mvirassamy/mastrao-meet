#!/bin/sh
# Prove Mastrao transcription tasks are isolated on a dedicated Celery worker.
set -eu

root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
workdir="$(mktemp -d)"
cleanup() {
  rm -f "$workdir"/values.yaml "$workdir"/disabled.yaml "$workdir"/backend.yaml "$workdir"/admission.yaml "$workdir"/mastrao.yaml "$workdir"/native-asr.yaml "$workdir"/transcribe.yaml "$workdir"/disabled-out.yaml "$workdir"/disabled-asr-out.yaml
  rmdir "$workdir"
}
trap cleanup EXIT

render() {
  values="$1"
  template="$2"
  output="$3"
  allow_empty="${4:-false}"
  if command -v helm >/dev/null 2>&1; then
    if helm template meet "$root/meet" -f "$values" -s "$template" > "$output"; then
      return
    fi
    [ "$allow_empty" = "true" ] && [ ! -s "$output" ] && return
    return 1
  fi
  if command -v docker >/dev/null 2>&1; then
    if docker run --rm -v "$root:/work" -v "$workdir:/values" alpine/helm:3.18.4 \
      template meet /work/meet -f "/values/$(basename "$values")" \
      -s "$template" > "$output"; then
      return
    fi
    [ "$allow_empty" = "true" ] && [ ! -s "$output" ] && return
    return 1
  fi
  echo "helm_or_docker_required" >&2
  exit 1
}

: > "$workdir/values.yaml"
cat > "$workdir/disabled.yaml" <<'EOF'
celeryMastraoTranscription:
  enabled: false
EOF

render "$workdir/values.yaml" templates/celery_mastrao_transcription_deployment.yaml "$workdir/mastrao.yaml"
render "$workdir/values.yaml" templates/celery_mastrao_native_asr_deployments.yaml "$workdir/native-asr.yaml"
render "$workdir/values.yaml" templates/celery_mastrao_native_admission_deployment.yaml "$workdir/admission.yaml"
render "$workdir/values.yaml" templates/celery_backend_deployment.yaml "$workdir/backend.yaml"
render "$workdir/values.yaml" templates/celery_transcribe_deployment.yaml "$workdir/transcribe.yaml"
render "$workdir/disabled.yaml" templates/celery_mastrao_transcription_deployment.yaml "$workdir/disabled-out.yaml" true
render "$workdir/disabled.yaml" templates/celery_mastrao_native_asr_deployments.yaml "$workdir/disabled-asr-out.yaml" true

python3 - "$workdir/mastrao.yaml" "$workdir/native-asr.yaml" "$workdir/admission.yaml" "$workdir/backend.yaml" "$workdir/transcribe.yaml" "$workdir/disabled-out.yaml" "$workdir/disabled-asr-out.yaml" <<'PY'
import re
import sys
from pathlib import Path


def worker_queues(manifest):
    arguments = re.findall(r"^\s*-\s+['\"]?-Q['\"]?\s*\n\s*-\s+([^\n]+)", manifest, re.MULTILINE)
    flags = re.findall(r"^\s*-\s+['\"]?(?:-Q|--queues)", manifest, re.MULTILINE)
    if len(arguments) != 1 or len(flags) != 1:
        raise SystemExit("worker_queue_argument_missing_or_ambiguous")
    return set(arguments[0].strip().strip("\"'").split(","))


mastrao, native_asr, admission, backend, transcribe, disabled, disabled_asr = (Path(p).read_text(encoding="utf8") for p in sys.argv[1:])
if worker_queues(mastrao) != {"mastrao-transcription"}:
    raise SystemExit("dedicated_worker_wrong_queue")
if "--concurrency=1" not in mastrao:
    raise SystemExit("concurrency_missing")
if "--prefetch-multiplier=1" not in mastrao:
    raise SystemExit("transcription_prefetch_missing")
lanes = [document for document in native_asr.split("---") if "kind: Deployment" in document]
if len(lanes) != 2 or {next(iter(worker_queues(lane))) for lane in lanes} != {
    "mastrao-native-asr-ready", "mastrao-native-asr-retry"
}:
    raise SystemExit("native_asr_lanes_missing")
if any("--prefetch-multiplier=1" not in lane or "--concurrency=1" not in lane for lane in lanes):
    raise SystemExit("native_asr_prefetch_or_concurrency_missing")
for manifest in (mastrao, backend, *lanes):
    for name in (
        "MASTRAO_NATIVE_ASR_GLOBAL_LIMIT",
        "MASTRAO_NATIVE_ASR_TENANT_LIMIT",
        "MASTRAO_NATIVE_ASR_MEETING_LIMIT",
    ):
        if not re.search(rf'^\s*- name: "{name}"\n\s*value: "4"$', manifest, re.MULTILINE):
            raise SystemExit("native_asr_capacity_configuration_missing")
if "mastrao-transcription" in worker_queues(backend):
    raise SystemExit("generic_worker_consumes_mastrao_queue")
if worker_queues(backend) & {"mastrao-native-asr-ready", "mastrao-native-asr-retry"}:
    raise SystemExit("generic_worker_consumes_native_asr_queue")
if worker_queues(admission) != {"mastrao-native-admission"}:
    raise SystemExit("native_admission_worker_wrong_queue")
if "mastrao-native-admission" in worker_queues(backend):
    raise SystemExit("generic_worker_consumes_native_admission_queue")
if "mastrao-transcription" in worker_queues(transcribe):
    raise SystemExit("summary_transcribe_consumes_mastrao_queue")
if any(secret in mastrao + native_asr for secret in ("MISTRAL_ASR_API_KEY", "OPENAI_ASR_API_KEY")):
    raise SystemExit("provider_secret_in_meet_worker")
if "kind: Deployment" in disabled:
    raise SystemExit("disabled_worker_still_rendered")
if "kind: Deployment" in disabled_asr:
    raise SystemExit("disabled_native_asr_worker_still_rendered")
print("PASS: dedicated mastrao-transcription worker isolation")
PY
