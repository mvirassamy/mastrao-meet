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

## Synthetic transcription canary

The three `*-transcription-synthetic-canary.patch.yaml` files are strategic
merge patches for the staging workloads involved in native transcription. They
enable capture and ASR while keeping the gateway restricted to synthetic test
data. They must not be used for beta-user audio while
`ASR_GATEWAY_NATIVE_TEST_DATA_ONLY=true`.

Render every document against the live API before applying it with
`kubectl patch --type=strategic --dry-run=server`. Apply the documents one at a
time, wait for `meet-api`, `worker-native`, and `asr-gateway` to complete their
rollouts, then run only the owned synthetic transcription journey.

If the canary fails, apply the two `*-rollback.patch.yaml` files. The rollback
deliberately leaves the gateway's synthetic-data-only guard enabled.
