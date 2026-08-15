#!/bin/sh
set -eu

root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

render_chart() {
  release="$1"
  chart="$2"
  values="$3"
  output="$4"
  if command -v helm >/dev/null 2>&1; then
    helm template "$release" "$chart" -f "$values" > "$output"
    return
  fi
  if command -v docker >/dev/null 2>&1; then
    chart_relative="${chart#"$root/"}"
    values_relative="${values#"$root/"}"
    docker run --rm -v "$root:/work" alpine/helm:3.18.4 \
      template "$release" "/work/$chart_relative" \
      -f "/work/$values_relative" > "$output"
    return
  fi
  echo "helm_or_docker_required" >&2
  exit 1
}

for environment in staging production; do
  handoff="$root/recording-handoff/$environment.yaml"
  meet_values="$root/env.d/$environment/values.meet.yaml.gotmpl"
  egress_values="$root/env.d/$environment/values.egress.yaml.gotmpl"

  test -f "$handoff"
  test -f "$meet_values"
  test -f "$egress_values"
  grep -q "environment: $environment" "$handoff"
  grep -q "atomicDeploymentRequired: true" "$handoff"
  grep -q "MASTRAO_MEETING_RECORDING_START_ENABLED: False" "$meet_values"
  grep -q "MASTRAO_MEETING_RECORDING_ARTIFACT_ACCESS_ENABLED: False" "$meet_values"
  grep -q "reconcile-mastrao-recordings" "$meet_values"
  grep -q -- "--limit 2" "$meet_values"
  grep -q "existingConfigSecret:" "$egress_values"
  grep -q "mastrao.io/recording-egress" "$egress_values"

  rendered_egress="$(mktemp)"
  rendered_meet="$(mktemp)"
  trap 'rm -f "$rendered_egress" "$rendered_meet"' EXIT
  render_chart recording-egress "$root/egress" "$egress_values" "$rendered_egress"
  render_chart meet "$root/meet" "$meet_values" "$rendered_meet"
  grep -q "automountServiceAccountToken: false" "$rendered_egress"
  grep -q "kind: PodDisruptionBudget" "$rendered_egress"
  grep -q "reconcile-mastrao-recordings" "$rendered_meet"
  grep -q "concurrencyPolicy: Forbid" "$rendered_meet"
  rm -f "$rendered_egress" "$rendered_meet"
  trap - EXIT
done
