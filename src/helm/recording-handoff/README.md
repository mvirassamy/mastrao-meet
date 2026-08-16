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
