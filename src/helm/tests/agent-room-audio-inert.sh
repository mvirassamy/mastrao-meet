#!/bin/sh
# Prove the room-audio worker is inert by default and secret-safe when enabled.
set -eu

root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
chart="$root/meet"
fixture="$root/tests/fixtures/agent-room-audio.yaml"
template="templates/agent_room_audio_deployment.yaml"

render_chart() {
  helm template meet "$chart" "$@"
}

expect_render_failure() {
  expected="$1"
  shift
  if output="$(render_chart "$@" 2>&1)"; then
    echo "unsafe_room_audio_values_rendered: $expected" >&2
    exit 1
  fi
  printf '%s\n' "$output" | grep -Fq "$expected"
}

default_rendered="$(render_chart)"
if printf '%s\n' "$default_rendered" | grep -Fq 'agent-room-audio'; then
  echo "room_audio_worker_rendered_by_default" >&2
  exit 1
fi

enabled_rendered="$(render_chart -f "$fixture" -s "$template")"
image="rg.fr-par.scw.cloud/mastrao-staging/meet-agents@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
printf '%s\n' "$enabled_rendered" | grep -Fq "image: \"$image\""
printf '%s\n' "$enabled_rendered" | grep -A 4 'name: "LIVEKIT_API_SECRET"' | grep -Fq 'secretKeyRef:'
printf '%s\n' "$enabled_rendered" | grep -A 4 'name: "AWS_S3_ACCESS_KEY_ID"' | grep -Fq 'secretKeyRef:'
printf '%s\n' "$enabled_rendered" | grep -A 4 'name: "AWS_S3_SECRET_ACCESS_KEY"' | grep -Fq 'secretKeyRef:'
printf '%s\n' "$enabled_rendered" | grep -A 4 'command:' | grep -Fq 'room_audio_worker.py'
printf '%s\n' "$enabled_rendered" | grep -A 1 'name: "LIVEKIT_URL"' | grep -Fq 'wss://livekit.internal'

secret_error="agentRoomAudio.envVars.LIVEKIT_API_SECRET must be only a secretKeyRef"
expect_render_failure "$secret_error" \
  -f "$fixture" \
  --set-string agentRoomAudio.envVars.LIVEKIT_API_SECRET=literal-secret \
  -s "$template"

missing_error="agentRoomAudio.envVars.ROOM_AUDIO_EFFECT_ISSUER is required"
expect_render_failure "$missing_error" \
  --set agentRoomAudio.enabled=true \
  -s "$template"

echo "agent_room_audio_inert_contract_ok"
