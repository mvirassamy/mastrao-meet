# VISIO OBS-1 / K8S-1 alert qualification

This is declarative preparation. It does not install rules, configure a
receiver, qualify staging, start Gate 1, or change recording policy.

## Coverage audit

Audit against the fetched `develop` sources on 2026-10-17: Meet `9ac355288`,
Platform `ff48fe89` and Deployments `fb76feee` (also main `35f2001`):

| Required signal | Delivered source | Alert coverage |
| --- | --- | --- |
| Native ready/retry depth | Meet `/metrics`: `mastrao_native_asr_queue_pending{lane}` | Four queue rules prepared here; thresholds and installation unproven. |
| Native ready/retry due age | Meet `/metrics`: `mastrao_native_asr_queue_oldest_due_age_seconds{lane}` | Separate age ceiling per lane; same missing approvals. |
| VISIO Pods Pending | Requires proven kube-state-metrics scrape and workload ownership mapping | No matching declarative rule found; runtime series and hold duration unproven. |
| DB saturation | Passive PostgreSQL receipt in `docs/installation/pgbouncer-transaction-pooling.md` | No DB saturation exporter or rule found; SQL snapshots are not monitoring time series. |

Platform's `infra/helm/asr-gateway/templates/prometheusrule.yaml` covers Gateway
availability, restarts, **PVC** Pending and Gateway application metrics. PVC
Pending is not Pod Pending. Gateway backlog is not the native ready/retry
queue. Its thresholds do not establish native queue or DB thresholds.
Deployments' `docs/gateway-k8s-storage-qualification.md` is a Gateway placement
runbook, not an alert declaration. Cluster installation and external monitoring
rules remain unverified by this source audit.

The queue values `1` item and `30` seconds in
`native-asr-autoscaling.md` are KEDA replica targets, **not alert ceilings**.
No alert ceiling or hold duration is inferred from them. The DB requirement of
23 application connections plus one readback slot is a qualification budget,
not a saturation percentage or alert hold duration.

## Queue policy: operator inputs required

Meet owns these metric names and its Helm chart owns the optional queue
`PrometheusRule`. `nativeAsrAlerts: {}` emits no resource. A policy requires
all of the following; there are no numerical policy defaults:

| Value | Evidence required from the operator |
| --- | --- |
| `service` | Exact backend `service` label observed in Prometheus. |
| `routingOwner`, `severity` | Labels matched by an existing Alertmanager receiver. |
| `prometheusRuleLabels` | Actual Prometheus `ruleSelector` labels; empty only if that selector allows it. |
| `ready.pendingMax`, `retry.pendingMax` | Approved non-negative integer ceiling, in eligible due items, independently per lane. |
| `ready.oldestDueAgeMaxSeconds`, `retry.oldestDueAgeMaxSeconds` | Approved non-negative integer ceiling in seconds, independently per lane. |
| `ready.for`, `retry.for` | Approved continuous exceedance duration per lane: number plus `s`, `m`, `h`, etc.; `0s` means immediate. |

The expression is `max(scoped gauge) > ceiling`, sustained for the configured
lane duration. Equality does not fire. `max` avoids counting the same database
queue once per backend replica. Both `namespace` (the Helm release namespace)
and `service` must be attached by the scraper; the app exports only `lane`.
If the installation uses different labels, reconcile the scrape binding before
selecting this policy. Do not guess a selector. A missing series produces no
queue alert and is not a healthy zero.

Depth and due age include live dispatch reservations and claims; they are not
waiting-only metrics. Age excludes future retries and starts at `asr_next_at`,
not meeting closure. Read `mastrao_native_asr_queue_active` alongside them.
The Meet owner and monitoring operator need passive historical series for both
lanes, scrape/evaluation intervals, normal/retry drain times and current worker
limits/admission policy to approve ceilings and durations. If those observations
are insufficient, keep the policy empty and record the missing observation
window. This change does not authorize load to obtain measurements.

After those inputs are approved in a separate operator change, render offline:

```bash
helm template "$MEET_RELEASE" src/helm/meet \
  --namespace "$STAGING_NAMESPACE" \
  --values "$APPROVED_BASE_VALUES" \
  --values "$APPROVED_ALERT_POLICY" \
  --show-only templates/native_asr_prometheusrule.yaml
python src/helm/tests/native-asr-alerts.py -v
```

Static test thresholds are synthetic fixtures, not staging recommendations.
No staging or production overlay selects a policy. Rendering does not install
the Operator CRD, prove evaluation or deliver notifications. A monitoring
operator must prove the Prometheus rule namespace/label selectors, loaded
expressions, target health and receiver route before declaring alerts operational.
See [Prometheus alert semantics](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/).

## Missing Pod and DB evidence

The monitoring operator must supply the approved Prometheus read endpoint and
authentication through the protected route. Read existing alerts using
`GET /api/v1/rules?type=alert`, scrape targets using
`GET /api/v1/targets?state=active`, and existing series using
`GET /api/v1/query`. A Meet `/metrics` HTTP 200 alone proves none of those
bindings. These API reads generate no workload traffic.

For **Pods Pending**, request instant queries for
`kube_pod_status_phase{namespace="<VISIO namespace>",phase="Pending"}` and
`kube_pod_status_unschedulable{namespace="<VISIO namespace>"}`, plus
`kube_pod_owner` and ReplicaSet-to-Deployment ownership series. The operator
must identify the precise VISIO worker/API deployments, distinguish unschedulable
Pods from normal startup/image pulls, and supply historical Pending durations,
scrape interval, approved hold duration, severity and route. Absent series are
missing evidence, not zero Pending Pods. No `5m` Pod threshold is borrowed from
the unrelated Gateway PVC rule.

For **DB saturation**, the DB owner must identify a delivered exporter or managed
monitoring series with exact names, types, labels and sample interval. Required
observations are current/peak connections per application role, finite role
limits, global connections and usable slots after reserved slots, capacity
refusal increments, and waiting clients/pool wait time **if a pooler exists**.
Include the collection window, counter resets and role visibility; partial
visibility cannot qualify a rule. A role limit of 5 against a required 24 is a
configuration qualification failure even with ample global slots. Correcting
that limit and defining a saturation alert are separate changes.

The DB owner must approve the saturation ceiling/headroom, hold duration,
refusal-counter window and receiver after measurements are available. Do not
invent `pg_*` / PgBouncer metric names or add DB SQL to Meet `/metrics` to mask
the missing source. The existing passive DB runbook specifies the SQL receipt
and stop conditions; this change does not execute it.

Stop activation if threshold/hold approval, exact target selection, exporter
series, Operator CRD/selector binding or receiver route is missing. Handoff
owners are the **Meet service owner** (queue policy), **Kubernetes/monitoring
operator** (Pod series and rule/receiver binding), and **DB owner** (DB exporter
and saturation policy). Their named people/contact channel and approved
monitoring access are absent from these repositories. No live alert inventory
was accessible from this checkout.
