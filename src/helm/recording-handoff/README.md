# Meeting recording deployment handoff

These manifests bind the recording-only Meet overlay and the dedicated Egress
chart for one environment. They are inputs to the trusted deployment executor,
not standalone Helmfile environments.

The executor must supply the normal production-safe Meet base values and the
named Egress configuration Secret, then render and deploy both components as
one reviewed operation. The development `common.yaml.gotmpl` values are never a
base for staging or production.

Both recording overlays start dark. Enabling capture or artifact access is a
separate human-authorized operation. The reconciliation CronJob remains present
through normal rollback.

## Staging video invitation endpoints

`staging.yaml` selects `env.d/staging/values.meet.yaml.gotmpl`. Its
`backend.envVars` supplies two private Core endpoints to the backend chart:

| Variable | POST path on `http://cabinet-core:8080` |
| --- | --- |
| `MASTRAO_CORE_VIDEO_DELIVERY_ENDPOINT` | `/internal/v1/meetings/video-invitations/delivery` |
| `MASTRAO_CORE_VIDEO_PARTICIPANT_ENDPOINT` | `/internal/v1/meetings/video-invitations/participant` |

These endpoints require the coordinated Meet invitation and Core video
contracts. Their presence does not enable capture or change rollout flags.

The 2026-10-06 staging audit found bootstrap annotations on `meet-api` and no
Helm ownership marker. The previous delivery receipt proves its image and
source, but does not prove that its bootstrap consumed this overlay. The
canonical bootstrap input has not been recovered. The operator must bind these
values to that input before claiming that the live configuration is durable.
The preparatory Platform generator is
`scripts/cabinet-core/qualification/kapsule-r5-package.ts`; its `meet-api`
environment must supply the identical two values if that packaging is used.

For the coordinated deployment, preserve the existing relay rules and add the
two exact POST paths from Platform's
`scripts/cabinet-core/qualification/kapsule-r5-relays.ts`. The audited Core
Deployment mounts `meet-core-relay-short-links-15f0db81`, which lacks both paths.
Prepare a new immutable relay ConfigMap, the Core volume reference change and
the two Meet API environment additions as one reviewed delivery. Record the
actual Deployment UID, resourceVersion, previous environment and ConfigMap
reference in the delivery receipt. No Kubernetes change is made by this overlay.

Rollback restores the captured environment and relay reference together,
guarded by the actual deployed UID and resourceVersion. Retain the previous
ConfigMap until verification completes; do not replay an older delivery patch.
