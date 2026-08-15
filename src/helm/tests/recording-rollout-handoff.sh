#!/bin/sh
set -eu

root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

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

  if command -v helm >/dev/null 2>&1; then
    rendered="$(mktemp)"
    trap 'rm -f "$rendered"' EXIT
    helm template recording-egress "$root/egress" -f "$egress_values" > "$rendered"
    grep -q "automountServiceAccountToken: false" "$rendered"
    grep -q "kind: PodDisruptionBudget" "$rendered"
    rm -f "$rendered"
    trap - EXIT
  fi
done
