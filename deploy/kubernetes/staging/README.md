# Meet staging network policy

`meet-api-network-policy.json` is the canonical staging network policy for the
Meet API. It matches the deployed policy and keeps the OIDC exchange limited to
`app.mastrao-staging.com` over HTTPS.

`agent-subtitles-network-policy.json` is the canonical staging network policy
for the live subtitle agent. It allows LiveKit DNS and signaling/media traffic,
plus DNS and HTTPS access to `api.openai.com`.

Validate the rendered object before applying it:

```bash
kubectl --context "$KUBE_CONTEXT" apply \
  --server-side --dry-run=server \
  --field-manager=mastrao-meet-staging \
  -f deploy/kubernetes/staging/meet-api-network-policy.json
```

Review `kubectl diff` against the same file before the supervised apply. A Meet
image rollout updates only the Deployments and must leave this policy intact.

Validate the subtitle agent policy as JSON and against the staging API server:

```bash
jq empty deploy/kubernetes/staging/agent-subtitles-network-policy.json

kubectl --context "$KUBE_CONTEXT" apply \
  --server-side --dry-run=server \
  --field-manager=mastrao-meet-staging \
  -f deploy/kubernetes/staging/agent-subtitles-network-policy.json
```

Review the live difference before applying it:

```bash
kubectl --context "$KUBE_CONTEXT" diff \
  --server-side \
  --field-manager=mastrao-meet-staging \
  -f deploy/kubernetes/staging/agent-subtitles-network-policy.json
```

Apply the reviewed policy:

```bash
kubectl --context "$KUBE_CONTEXT" apply \
  --server-side \
  --field-manager=mastrao-meet-staging \
  -f deploy/kubernetes/staging/agent-subtitles-network-policy.json
```
