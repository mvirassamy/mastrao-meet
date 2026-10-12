# Native ASR KEDA autoscaling

Meet keeps `mastrao-native-asr-ready` and `mastrao-native-asr-retry` on
separate Celery workers. Each worker has concurrency 1 and prefetch 1. Their
shared admission limits remain the existing
`MASTRAO_NATIVE_ASR_GLOBAL_LIMIT`, `MASTRAO_NATIVE_ASR_TENANT_LIMIT`, and
`MASTRAO_NATIVE_ASR_MEETING_LIMIT`; KEDA never changes or replaces them.

The chart can render one KEDA `ScaledObject` per lane. Both are disabled by
default because installing KEDA is a separate cluster operation. The chart
does not install KEDA, its CRDs, an HPA adapter, a metrics provider, or a
database Secret.

Migration `0055_native_asr_queue_metrics` creates
`meet_mastrao_native_asr_queue_metrics` with exactly one row per lane and four
columns: `lane`, `pending_count`, `oldest_due_age_seconds`, and `active_count`.
`pending_count` includes eligible due work, including live dispatch reservations
and claims. `oldest_due_age_seconds` is the whole number of seconds since the
oldest included `asr_next_at`. `active_count` counts included work with a live
reservation or claim once, even if both leases are present. Empty lanes return
zero for all three metrics.

Live leases retain their dispatch lane. After both leases expire, first attempts
return to ready; work with a prior attempt, error, or durable receipt returns to
retry. Future retries, acknowledged work, exhausted attempts, expired retention,
missing source receipts, conflicted epochs, and uncorrelated connections are
excluded. The view and KEDA queries expose no tenant, meeting, room, capture, or
source identifiers.

Each ScaledObject has two PostgreSQL triggers over this view: `pending_count`
with `targetQueryValue: "1"`, and `oldest_due_age_seconds` with
`targetQueryValue: "30"`. Both use `activationTargetQueryValue: "0"` and the same
read-only authentication. KEDA/HPA takes the largest replica recommendation,
clamped to the default minimum of 1 and maximum of 2. Two pending items or an
oldest age of 60 seconds therefore request two workers; satisfying both signals
is not required. `active_count` is available for observing lane occupancy and
is not a third trigger. These metrics come from durable PostgreSQL state.

## Enabling a lane

Install KEDA separately, then create a namespaced `TriggerAuthentication`
before enabling either lane. It must expose the `connection` parameter from an
existing Secret containing a PostgreSQL connection string. Use a dedicated
metrics account with only `CONNECT`, schema `USAGE`, and `SELECT` on
`meet_mastrao_native_asr_queue_metrics`. The view exposes only the lane and its
three aggregate metrics; the account does not need access to the source tables.
Apply migration `0055` before enabling either lane. Do not reuse the Meet
application database credentials and do not add this Secret to a Meet
Deployment.

For example, the platform operator owns this resource and its referenced
Secret; neither belongs in this chart:

```yaml
apiVersion: keda.sh/v1alpha1
kind: TriggerAuthentication
metadata:
  name: meet-native-asr-postgresql
  namespace: meet
spec:
  secretTargetRef:
    - parameter: connection
      name: meet-native-asr-keda-postgresql
      key: connection
```

After the account, Secret, and `TriggerAuthentication` exist, enable one or
both independent lanes in the deployment values:

```yaml
celeryMastraoNativeAsrReady:
  autoscaling:
    enabled: true
    triggerAuthenticationName: meet-native-asr-postgresql
    minReplicas: 1
    maxReplicas: 2

celeryMastraoNativeAsrRetry:
  autoscaling:
    enabled: true
    triggerAuthenticationName: meet-native-asr-postgresql
    minReplicas: 1
    maxReplicas: 2
```

The default PostgreSQL polling interval is 30 seconds. With one minimum replica,
the cluster HPA sync period controls scale-up checks. Five-minute HPA scale-down
stabilization preserves one worker in each enabled lane and prevents rapid
scale-down. Removing a ScaledObject restores the chart replica count. Change
replica bounds only after confirming the existing global, tenant, and meeting
limits and the ASR provider capacity remain appropriate.

Validate the rendered objects with
`src/helm/tests/mastrao-transcription-queue.sh`. Also verify the separate KEDA
installation can read the dedicated Secret and execute both aggregate queries
before enabling production values.

PostgreSQL trigger metadata follows the [KEDA PostgreSQL scaler documentation](https://keda.sh/docs/2.18/scalers/postgresql/).
