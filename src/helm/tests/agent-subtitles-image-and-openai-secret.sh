#!/bin/sh
# Prove immutable agent images and secret-only OpenAI credentials render safely.
set -eu

root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
fixture="$root/tests/fixtures/agent-subtitles-openai.yaml"
template="templates/agent_subtitles_deployment.yaml"

render_chart() {
  if command -v helm >/dev/null 2>&1; then
    helm template meet "$root/meet" "$@" -s "$template"
    return
  fi
  if command -v docker >/dev/null 2>&1; then
    docker run --rm -v "$root:/work" alpine/helm:3.18.4 \
      template meet /work/meet "$@" -s "$template"
    return
  fi
  echo "helm_or_docker_required" >&2
  exit 1
}

if command -v helm >/dev/null 2>&1; then
  digest_rendered="$(render_chart -f "$fixture")"
else
  digest_rendered="$(render_chart -f /work/tests/fixtures/agent-subtitles-openai.yaml)"
fi
tag_rendered="$(render_chart)"

digest="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
image="rg.fr-par.scw.cloud/mastrao-staging/meet-agents@$digest"

printf '%s\n' "$digest_rendered" | grep -Fq "image: \"$image\""
printf '%s\n' "$digest_rendered" | grep -A 5 'name: "OPENAI_API_KEY"' | grep -Fq 'secretKeyRef:'
printf '%s\n' "$digest_rendered" | grep -A 5 'name: "OPENAI_API_KEY"' | grep -Fq 'name: meet-agent-openai'
printf '%s\n' "$digest_rendered" | grep -A 5 'name: "OPENAI_API_KEY"' | grep -Fq 'key: OPENAI_API_KEY'
if printf '%s\n' "$digest_rendered" | grep -A 1 'name: "OPENAI_API_KEY"' | grep -Fq 'value:'; then
  echo "openai_key_rendered_as_plain_value" >&2
  exit 1
fi
printf '%s\n' "$tag_rendered" | grep -Fq 'image: "lasuite/meet-agents:latest"'

echo "agent_subtitles_image_and_openai_secret_ok"
