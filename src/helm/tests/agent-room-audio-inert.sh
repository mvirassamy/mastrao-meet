#!/bin/sh
# Prove the room-audio worker is inert by default and secret-safe when enabled.
set -eu

root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
chart="$root/meet"
fixture="$root/tests/fixtures/agent-room-audio.yaml"
live_fixture="$root/tests/fixtures/agent-room-audio-live.yaml"
post_template="templates/agent_room_audio_deployment.yaml"
live_template="templates/agent_room_audio_live_deployment.yaml"

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

enabled_rendered="$(render_chart -f "$fixture" -s "$post_template")"
image="rg.fr-par.scw.cloud/mastrao-staging/meet-agents@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
printf '%s\n' "$enabled_rendered" | grep -Fq "image: \"$image\""
printf '%s\n' "$enabled_rendered" | grep -A 4 'name: "LIVEKIT_API_SECRET"' | grep -Fq 'secretKeyRef:'
printf '%s\n' "$enabled_rendered" | grep -A 4 'name: "AWS_S3_ACCESS_KEY_ID"' | grep -Fq 'secretKeyRef:'
printf '%s\n' "$enabled_rendered" | grep -A 4 'name: "AWS_S3_SECRET_ACCESS_KEY"' | grep -Fq 'secretKeyRef:'
if printf '%s\n' "$enabled_rendered" | grep -Fq 'MISTRAL_ASR_API_KEY'; then
  echo "post_worker_received_live_provider_secret" >&2
  exit 1
fi
printf '%s\n' "$enabled_rendered" | grep -A 4 'command:' | grep -Fq 'room_audio_worker.py'
printf '%s\n' "$enabled_rendered" | grep -A 1 'name: "LIVEKIT_URL"' | grep -Fq 'wss://livekit.internal'
printf '%s\n' "$enabled_rendered" | grep -A 1 'name: ROOM_AUDIO_PIPELINE' | grep -Fq 'post'

live_rendered="$(render_chart -f "$live_fixture" -s "$live_template")"
printf '%s\n' "$live_rendered" | grep -Fq "image: \"$image\""
printf '%s\n' "$live_rendered" | grep -A 4 'name: "LIVEKIT_API_SECRET"' | grep -Fq 'secretKeyRef:'
printf '%s\n' "$live_rendered" | grep -A 4 'name: "MISTRAL_ASR_API_KEY"' | grep -Fq 'secretKeyRef:'
printf '%s\n' "$live_rendered" | grep -A 1 'name: ROOM_AUDIO_PIPELINE' | grep -Fq 'live'
if printf '%s\n' "$live_rendered" | grep -Eq 'AWS_S3_|ROOM_AUDIO_SOURCE_CALLBACK'; then
  echo "live_worker_received_post_pipeline_secret" >&2
  exit 1
fi

secret_error="agentRoomAudio.envVars.LIVEKIT_API_SECRET must be only a secretKeyRef"
expect_render_failure "$secret_error" \
  -f "$fixture" \
  --set-string agentRoomAudio.envVars.LIVEKIT_API_SECRET=literal-secret \
  -s "$post_template"

mistral_error="agentRoomAudioLive.envVars.MISTRAL_ASR_API_KEY must be only a secretKeyRef"
expect_render_failure "$mistral_error" \
  -f "$live_fixture" \
  --set-string agentRoomAudioLive.envVars.MISTRAL_ASR_API_KEY=literal-secret \
  -s "$live_template"

post_secret_error="agentRoomAudioLive.envVars.AWS_S3_SECRET_ACCESS_KEY is forbidden"
expect_render_failure "$post_secret_error" \
  -f "$live_fixture" \
  --set-string agentRoomAudioLive.envVars.AWS_S3_SECRET_ACCESS_KEY=forbidden \
  -s "$live_template"

live_secret_error="agentRoomAudio.envVars.MISTRAL_ASR_API_KEY is forbidden"
expect_render_failure "$live_secret_error" \
  -f "$fixture" \
  --set-string agentRoomAudio.envVars.MISTRAL_ASR_API_KEY=forbidden \
  -s "$post_template"

legacy_error="agentRoomAudioLive requires agentSubtitles.replicas=0"
expect_render_failure "$legacy_error" \
  -f "$live_fixture" \
  --set agentSubtitles.replicas=1 \
  -s "$live_template"

missing_error="agentRoomAudio.envVars.ROOM_AUDIO_EFFECT_ISSUER is required"
expect_render_failure "$missing_error" \
  --set agentRoomAudio.enabled=true \
  -s "$post_template"

echo "agent_room_audio_inert_contract_ok"
