# Meet staging network policy

`meet-api-network-policy.json` is the canonical staging network policy for the
Meet API. It matches the deployed policy and keeps the OIDC exchange limited to
`app.mastrao-staging.com` over HTTPS.

Validate the rendered object before applying it:

```bash
kubectl --context "$KUBE_CONTEXT" apply \
  --server-side --dry-run=server \
  --field-manager=mastrao-meet-staging \
  -f deploy/kubernetes/staging/meet-api-network-policy.json
```

Review `kubectl diff` against the same file before the supervised apply. A Meet
image rollout updates only the Deployments and must leave this policy intact.

## Live transcription worker placement

The staging `agent-subtitles` worker currently runs on the `capture` node pool
because the `general` pool reported `DiskPressure` during the live transcription
canary. This is a temporary operational placement. Keep
`agentSubtitles.nodeSelector` unset in the chart defaults and move the worker
back to the intended general-purpose pool after its disk pressure is resolved.
