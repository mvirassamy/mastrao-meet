# Meet automatic staging preparation

This change prepares Meet for the existing GitHub Actions → GitOps PR →
Argo CD delivery path. It does not install an Application, change a writer,
enable an automatic trigger, run a migration or deploy Meet. The cross-repository
work is tracked in [Deployments #64](https://github.com/mvirassamy/mastrao-deployments/issues/64).

## Prepared publication signal

`.github/workflows/signal-staging-publication.yml` runs on completion of
`meet Workflow` on `develop`, this repository's protected default branch. Its
only job calls a dedicated Deployments reusable workflow at an immutable SHA
with empty `GITHUB_TOKEN` permissions and one explicitly named Signal secret.
No application code or receipt is executed with that identity.

The hook stays skipped unless repository variable
`MEET_DEPLOYMENTS_SIGNAL_ENABLED` is exactly `true`. The called workflow also
requires canonical repository, protected `develop`, completed successful run
and original `push` event. It sends only the validated run/attempt to the
existing selection receiver on protected Deployments `main`. That receiver
independently authenticates the three image receipts and still requires its own
`MEET_STAGING_PREPARATION_ENABLED` guard. Missing first selection remains blocked
for explicit owner review of native images and the two agents' convergence.

Merge the Deployments implementation first and update this caller's SHA to the
reviewed resulting `main` commit after any squash or rebase. Before a separately
approved activation, confirm private reusable-workflow access and the existing
Signal App's Actions-only access to Deployments. The called job uses Meet's
`meet-staging-delivery-signal` environment, restricted to protected `develop`,
with public `MEET_DEPLOYMENTS_SIGNAL_APP_ID` and private
`MEET_DEPLOYMENTS_SIGNAL_APP_PRIVATE_KEY` references. No key, environment,
permission or repository setting is provisioned by this source change.
See the [Deployments Signal procedure](https://github.com/mvirassamy/mastrao-deployments/blob/main/docs/meet-publication-signal.md)
for the separate activation prerequisites. Both guards remain closed here.
This hook does not activate Argo or prove automatic delivery.

## First delivery scope

The first useful release must prove a two-party meeting: authenticated entry,
room creation/join, bidirectional audio/video, reconnect and leave. An HTTP
health endpoint alone cannot qualify it. Platform and Tenant Core must expose
the compatible identity, room and authorization contracts, but their own
automatic deployment is not a prerequisite. Record their installed versions
when qualifying Meet.

The [staging browser recipe](meet-staging-browser-qualification.md) prepares
authenticated two-party join, synthetic bidirectional media, reload/rejoin and
leave checks against an existing dedicated room. It does not prove room
creation, network-outage recovery or exact deployed image/Argo correlation.

Recording, Egress, native ASR, subtitles and summaries form a second acceptance
scope. Do not disable or abandon an installed consumer merely because its
complete functional recipe is deferred. A release must preserve the configuration
and image compatibility of every active Meet consumer.

## Immutable image selection

The existing CI publishes three receipts from the same successful protected
`develop` run and attempt: `meet-frontend`, `meet-backend`, `meet-agents`.
See [candidate publication](staging-candidate-publication.md) for the fixed
recipes and qualification-before-publication sequence. An incomplete matrix,
mixed source/run/attempt or stale selection must not become a release.

The chart accepts `sha256:<64 lowercase hex characters>` in `image.digest`
and component `image.digest` values. A digest renders `repository@sha256:…`;
empty digest values preserve development tag rendering. Backend workers,
Jobs and CronJobs share `backend.image` with fallback to the root `image`.
An explicit component repository or tag without a digest does not inherit a
digest from another image. Set the component digest explicitly for GitOps.

| Image | Consumers to reconcile against the operator inventory |
| --- | --- |
| `meet-frontend` | Frontend web Deployment |
| `meet-backend` | API, migration/bootstrap Jobs, backend Celery, native admission, transcription, native ASR ready/retry and configured backend CronJobs |
| `meet-agents` | Subtitle agent and metadata collector, with their distinct commands |
| Existing summary images | Optional summary, transcribe and summarize consumers; not produced by the three-image publisher |

Summary image digest rendering is supported, but those images are **not** added
to the three-receipt release. Their presence, writer and compatibility need
operator confirmation. LiveKit, Redis and Egress keep their current management
unless the adoption review demonstrates that a change is necessary.

## Migrations before runtime replacement

Use the chart's existing migration Job with native Argo hooks for an explicitly
reviewed **additive and backward-compatible** migration. This is not permission
to run an arbitrary migration on every release. Before rendering a staging
Application, the operator must approve its database target, backup/restore
prerequisite and migration list against the installed state.

The staging values must set:

```yaml
backend:
  migrate:
    enabled: true
    activeDeadlineSeconds: 600 # Replace with the reviewed bounded duration.
    restartPolicy: Never
  migrateJobAnnotations:
    argocd.argoproj.io/hook: PreSync
    argocd.argoproj.io/hook-delete-policy: HookSucceeded
  jobs:
    backoffLimit: 0
    ttlSecondsAfterFinished: null
  createsuperuser:
    enabled: false
```

This example is not an installable staging values file. Supply all validated
runtime references and approved digests through the reviewed adoption change.
`activeDeadlineSeconds` bounds the whole Job, including database readiness.
A failed hook is retained for diagnosis; a retry requires preserving its
failure evidence and an operator decision before removing that named Job. Do not use `Replace=true,Force=true` on
the migration hook. Development defaults keep their previous behavior.

All hook dependencies—namespace, ServiceAccount, pull credentials, database
credentials and runtime configuration—must exist **before** PreSync. A negative
Sync wave is insufficient. A selective sync skips hooks and cannot qualify a
release. Hooks in this Application do not order another Application's updates.

Do not automatically reverse Django migrations on rollback. Review irreversible
data changes and partial non-atomic/concurrent-index failures separately.
After successful additive migrations, restoring an older image is allowed only
if its compatibility with the resulting schema has been established.

## Targeted operator handoff

The following bindings are still required; repository rendering cannot prove
their current values. Obtain them through the existing authorized operator
procedure, returning names, UIDs, image digests and sanitized status only.
Never return Secret values, tokens, database URLs or kubeconfig content.

| Binding | Evidence required before adoption |
| --- | --- |
| Operational ownership | Matthias is the infrastructure owner; identify the accountable Meet writer and the person approving transfer/rollback |
| Existing writer | Current release/controller/script and concurrent agents; establish one writer during the transfer without disabling unrelated workloads |
| Native resource identities | Namespaces, names, UIDs, selectors and scheduling constraints for API, frontend, every active worker/agent/Job/CronJob |
| Existing configuration | Secret/ConfigMap and pull-reference names/keys only; public route references and approved TLS/network path |
| Database and migration state | Applied migrations, pending reviewed list, transaction/pooler constraints, restore point and responsible operator |
| Argo destination | Instance, controller/reconciler identities, bounded namespace/resource permissions and effective allow/deny checks |
| Argo ownership | Application/AppProject, exact source revision/path, adoption preview preserving identities and Service selectors |
| Functional fixtures | Dedicated organization and two authorized participants, cleanup/renewal owner; credentials stay inside the verifier |
| Failure observation | Independent rollout deadline, image readback and hook result; a rollout failing before Healthy may never start PostSync |

Keep `observe`/promotion guards closed until these bindings are confirmed and
the single-writer transfer is authorized. If a backup prerequisite is blocked,
keep the migration and adoption blocked; do not create a new backup service or
search for credentials as part of this preparation.

## Acceptance and failure behavior

The eventual PostSync verifier must read the **installed** image IDs and their
owners for the selected consumers, compare them with the release's platform
manifest digests, then run the two-party functional recipe. An OCI index digest
and its `linux/amd64` child digest are distinct. Accept only their verified
relationship, never a matching tag alone. Qualify the exact Argo revision and
operation/Job/Pod that produced the result.

Report each layer independently: candidate published, selection accepted,
rollout observed, functional checks passed. A failed PostSync means deployed
but unqualified; it does not roll back the database or workload. Preserve the
failed hook and inspect it before retrying. No custom archive bucket is required;
the Job result and ordinary CI artifacts are sufficient for this initial pilot.

Before activation, a separate reviewed change must provide the actual bindings,
bounded verifier, admission/RBAC scope, failure signaling and rollback procedure.
No live adoption or automatic Meet delivery is claimed by this preparatory PR.
