# Meet staging network policies

`meet-api-network-policy.json` is the canonical staging network policy for the
Meet API. It matches the deployed policy and keeps the OIDC exchange limited to
`app.mastrao-staging.com` over HTTPS.

`agent-subtitles-network-policy.json` is the canonical policy for the live
subtitle agent. It permits only cluster DNS for LiveKit and OpenAI, LiveKit
signalling and media, and OpenAI HTTPS. The media ports are required for the
agent to join the room after accepting a dispatch.

Validate the rendered object before applying it:

```bash
kubectl --context "$KUBE_CONTEXT" apply \
  --server-side --dry-run=server \
  --field-manager=mastrao-meet-staging \
  -f deploy/kubernetes/staging/meet-api-network-policy.json \
  -f deploy/kubernetes/staging/agent-subtitles-network-policy.json
```

Review `kubectl diff` against the same files before the supervised apply. A Meet
image rollout updates only the Deployments and must leave these policies intact.
