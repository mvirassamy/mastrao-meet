# CI supply-chain debt

The verified-action pinning in the CI hardening change is intentionally partial. Before enabling any staging publication, resolve and review full commit SHAs for these remaining floating references:

- `azure/setup-helm@v4`
- `numerique-gouv/action-trivy-cache@main`
- `numerique-gouv/action-argocd-webhook-notification@main`
- `crowdin/github-action@v2`
- `actions/cache@v5`
- `astral-sh/setup-uv@v7`
- `docker/metadata-action@v5`
- `docker/login-action@v3`
- `docker/build-push-action@v6`

Do not activate staging publication until each reference has a verified SHA for the same semantic major, with its release version retained in a comment.
