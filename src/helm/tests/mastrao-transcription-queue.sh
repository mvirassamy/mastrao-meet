#!/bin/sh
# Prove Mastrao transcription tasks are isolated on a dedicated Celery worker.
set -eu

root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
workdir="$(mktemp -d)"
cleanup() {
  rm -f "$workdir"/values.yaml "$workdir"/disabled.yaml "$workdir"/backend.yaml "$workdir"/admission.yaml "$workdir"/mastrao.yaml "$workdir"/transcribe.yaml "$workdir"/disabled-out.yaml
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
render "$workdir/values.yaml" templates/celery_mastrao_native_admission_deployment.yaml "$workdir/admission.yaml"
render "$workdir/values.yaml" templates/celery_backend_deployment.yaml "$workdir/backend.yaml"
render "$workdir/values.yaml" templates/celery_transcribe_deployment.yaml "$workdir/transcribe.yaml"
render "$workdir/disabled.yaml" templates/celery_mastrao_transcription_deployment.yaml "$workdir/disabled-out.yaml" true

python3 - "$workdir/mastrao.yaml" "$workdir/admission.yaml" "$workdir/backend.yaml" "$workdir/transcribe.yaml" "$workdir/disabled-out.yaml" <<'PY'
import sys
from pathlib import Path

mastrao, admission, backend, transcribe, disabled = (Path(p).read_text(encoding="utf8") for p in sys.argv[1:])
if "mastrao-transcription" not in mastrao:
    raise SystemExit("dedicated_queue_not_enabled_by_default")
if "--concurrency=1" not in mastrao and '"--concurrency=1"' not in mastrao:
    raise SystemExit("concurrency_missing")
if "meet-backend" in mastrao.split("-Q", 1)[-1][:80] and "mastrao-transcription" not in mastrao:
    raise SystemExit("dedicated_worker_wrong_queue")
if "mastrao-transcription" in backend:
    raise SystemExit("generic_worker_consumes_mastrao_queue")
if "mastrao-native-admission" not in admission:
    raise SystemExit("native_admission_worker_missing_queue")
if "meet-backend" in admission.split("-Q", 1)[-1][:80]:
    raise SystemExit("native_admission_worker_consumes_backend_queue")
if "mastrao-native-admission" in backend:
    raise SystemExit("generic_worker_consumes_native_admission_queue")
if "mastrao-native-admission" in mastrao:
    raise SystemExit("transcription_worker_consumes_native_admission_queue")
if "mastrao-transcription" in transcribe:
    raise SystemExit("summary_transcribe_consumes_mastrao_queue")
if "MISTRAL_ASR_API_KEY" in mastrao or "OPENAI_ASR_API_KEY" in mastrao:
    raise SystemExit("provider_secret_in_meet_worker")
if "kind: Deployment" in disabled:
    raise SystemExit("disabled_worker_still_rendered")
print("PASS: dedicated mastrao-transcription worker isolation")
PY
