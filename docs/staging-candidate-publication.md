# Staging candidate publication

The `meet Workflow` CI (`.github/workflows/meet.yml`) is the only publication
entry point, on push or manual dispatch from `develop`. After its checks
succeed, it calls the reusable `Publish one Meet staging candidate` workflow
(`.github/workflows/staging-candidate.yml`) for `meet-frontend`, `meet-backend`
and `meet-agents`. Each call builds one image at the exact CI run SHA, scans
the resulting OCI archive, copies it to the Scaleway staging registry without
rebuilding and uploads a digest-pinned receipt.
It never deploys: every receipt records `PUBLISHED_NOT_DEPLOYED` and
`deploymentApplied: false`.

## Branches and environments

`develop` is the staging source branch; `main` is the production source
branch. The `meet Workflow` CI runs on pushes to both branches and on pull
requests targeting either branch.

A successful push or manual CI run on `develop` calls the local publisher for
`meet-frontend`, `meet-backend` and `meet-agents`. Each image is built from the
exact SHA checked by that run. Publication waits for changelog lint, root and
Helm contracts, mail generation, backend/agents/summary lint, backend/summary
tests, frontend lint/format/tests and SDK lint/format/build. PR-only commit
and changelog-change checks are not publication dependencies on push or
manual runs.

Pull requests and runs on `main` cannot publish staging candidates. Manual
publication uses the CI workflow with a full `source_sha` equal to the selected
`develop` revision (`github.sha`). It runs the same ten gates and publishes all
three images. The callee exposes only `workflow_call`; the standalone dispatch
that accepted other ancestor SHAs has been removed. Neither publication path
updates the application running in staging.

There is no production publication or deployment workflow in this repository.
A push to `main` runs CI only. Production automation is deferred. It will need
its own registry, credentials and release contract; the staging publisher
cannot publish production releases.

## Room Audio production evidence

`Prepare Room Audio production evidence` is a separate manual, read-only
workflow. It is closed to the immutable `meet-agents` and `meet-backend`
candidates selected by `PROD-MEET-PROMOTION-002`, plus the dedicated
`metadata-collector-production` Docker target at source commit
`9077554dbed793e14d0c334b8217e5cbefff7b8b` and the dedicated
`livekit-render-production` target from the protected workflow commit. For the
two published candidates,
it downloads the original V1 receipt from the historical Actions run and
copies the exact `repository@sha256` candidate into a temporary OCI layout.
For both dedicated targets, it verifies the immutable source tree and archive,
builds one local linux/amd64 OCI archive with the closed recipe and
deterministic source timestamp, and records a
`BUILT_NOT_PUBLISHED_NOT_DEPLOYED` receipt. It does not add either target to
the staging publication targets.

All four paths verify every manifest, config and layer descriptor and scan the
isolated linux/amd64 runtime with Trivy `0.74.0`. The workflow installs and
verifies the pinned Linux amd64 Trivy binary before registry authentication,
removes the registry credential immediately after the ORAS readback, and
records the verified binary digest in the evidence. The metadata collector and
LiveKit renderer jobs never receive or require that registry credential.

The workflow uploads the original or local-build receipt, a complete OCI graph
and one `room-audio-scan-provenance-v1` record. For `metadata-collector` and
`livekit-render`, the same artifact also retains the exact OCI archive produced
by the verified local build for one day. The workflow packages the archive,
receipt, graph, scan provenance and binding as five explicitly named files at
the artifact root. A binding record hashes every retained input after re-reading
the archive and proving that its complete OCI graph is unchanged. The archive
is hashed as a stream and both its stored and extracted size are limited to
2 GiB. This gives a later, separately authorized digest-only publisher the
original bytes without rebuilding them. The evidence workflow still has no
registry write credential or publication command.

Evidence is emitted only when
the receipt and graph match the closed candidate, the BuildKit attestation
contains exactly one in-toto SLSA v1 provenance statement whose subject is the runtime
manifest, and any source commit or build-recipe fields present match the closed
candidate. Trivy must report the exact config digest, an explicit vulnerability
list for every result and a known severity for every finding, a fresh database,
and no HIGH or CRITICAL finding. The evidence expiry is the earlier of the
60-minute scan limit and the 360-minute database limit.

The workflow has no publication command and cannot deploy, restart or activate
a workload. Its `room-audio-production-evidence` environment must be
restricted to protected `develop` and expose only a pre-existing
`MEET_STAGING_REGISTRY_READ_PASSWORD` identity with registry read access. Do
not reuse the staging publisher credential. Environment policy, credential
scope, the independent source-read authorization and the protected production
destination acceptance remain human-controlled prerequisites outside this
repository.

## What the workflow proves

- A credential-free validation job rejects foreign repositories, events other
  than push/manual dispatch, branches other than protected `develop`, malformed
  source SHAs and any publication of a SHA different from the CI run.
- The verification tooling (`scripts/ci`) is checked out from the workflow's
  own commit into `ci-tools/`, never from the candidate source, and is
  imported before anything is pushed.
- The candidate source is checked out alone into `source/`. Its SHA is 40
  lowercase hex characters, is the checked-out `HEAD`, is an ancestor of a
  freshly fetched `origin/develop` and leaves a clean worktree. Its Git tree
  and `git archive` SHA-256 are recorded.
- The build recipe (context, Dockerfile, target, repository and every build
  argument) comes from the closed table in
  `scripts/ci/staging_candidate_recipe.py`.
  The build step's inputs are fixed to that recipe (a unit test pins them),
  and the receipt rejects any recipe passed to the build that differs from
  the table. The receipt records the recipe passed to the build; what
  BuildKit actually used remains inspectable in the `mode=max` provenance.
- Every shell step runs with `bash -eo pipefail`, so a failing `git`
  command cannot yield an empty status or the hash of an empty archive.
- BuildKit exports an OCI archive, with `mode=max` provenance and SBOM. The
  local index must hash to the build output digest and contain one attested
  `linux/amd64` image, using the same validator as the remote readback.
- The archive is extracted to an OCI layout directory for Trivy (its OCI reader
  requires a directory, not a tar file). Its scan-only index selects the exact
  runtime manifest from the validated readback, so Trivy cannot select an
  attestation instead. The original archive remains unchanged for publication.
  Trivy scans the layout before registry login. Fixable
  HIGH or CRITICAL findings fail publication. This is an image vulnerability
  gate, not proof that a container starts or that a meeting works.
- The archive checksum is recorded before scanning and checked immediately
  before publication. ORAS copies the archive's exact index and its children
  by digest, including attestations; there is no second build or retagged
  Docker daemon export. The subsequent remote readback must match that digest.
- The registry readback hashes the raw index returned for
  `repository@digest` and rejects it unless the bytes match the digest, the
  index holds exactly one `linux/amd64` image, and an attestation manifest
  references that image.

The copy uses the standard [ORAS OCI layout support](https://oras.land/docs/commands/oras_cp/),
including tar archives and complete image indexes. The scan uses
[Trivy image archive input](https://trivy.dev/docs/latest/target/container_image/).
The existing V1 receipt shape remains unchanged. It proves a published
candidate, not the scan database's future freshness, the absence of every
vulnerability, an installed image, or application qualification.

For the separate GitOps adoption prerequisites, see
[Meet automatic staging preparation](meet-automatic-staging.md).

## Activation prerequisites

The source guard and job-level branch condition only protect the
copy of the workflow stored on `develop`. A workflow edited on another
branch could request the same environment, so the credential boundary must be
enforced by GitHub itself. In this order:

1. Protect `develop` with the CI checks and PR review policy before activation.
   The source guard requires GitHub to report the branch as protected.
2. Create the `staging-candidates` environment with deployment branches
   restricted to `develop` only. Existing reviewer rules still apply: if an
   approval is required, publication waits for it, including automatic runs.

   GitHub creates a missing environment **without** protection rules the
   first time a job references it, so do this before storing the secret and
   check the rules again if the environment already exists.
3. Store `MEET_STAGING_REGISTRY_PASSWORD` **only** as a secret of that
   environment. Never define it as a repository or organisation secret: the
   workflow cannot tell where the secret came from.
4. Keep the workflow on the default branch, `develop`. GitHub only offers
   `workflow_dispatch` for workflows present on the default branch; dispatch
   the CI workflow from `develop`, as required by its publication job.

The "Require the dedicated registry publisher" step only fails closed while
no `MEET_STAGING_REGISTRY_PASSWORD` is visible to the job. It does not check
reviewers or where the secret is stored: environment rules are the actual
credential boundary. The reusable caller passes only the named publisher
secret, without `secrets: inherit`; the publication job selects the environment
secret. This follows the
[GitHub reusable workflow secret rules](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows#using-inputs-and-secrets-in-a-reusable-workflow).

These prerequisites must be verified by the operator before merging the
activation. Local workflow checks do not prove the remote branch protection,
environment policy or publisher IAM scope.

## Running it

After fusion to `develop`, wait for CI and all three publication jobs, then
download each
`meet-<target>-<SHA>-run-<id>-attempt-<attempt>-candidate-receipt` artifact
for the selected run and attempt. Image tags are
`sha-<SHA>-run-<id>-attempt-<attempt>` so retries at the same source SHA
publish to separate tags and upload separate artifacts. The receipt filename
remains `candidate-receipt.json` and its V1 contract is unchanged. For a manual
publication, select `develop` in the `meet Workflow` dispatch and supply the
full SHA of that selected revision. The input must equal the SHA GitHub
records for the run; a different or malformed input fails before publication.
If the branch moves between reading its SHA and dispatching, use the new
selected revision and rerun the CI entry point. The callee cannot be
dispatched directly and no image target is selectable at the CI entry point.
Promote an image only by its `image.reference` (`repository@digest`).

A partial matrix failure can leave some images published. Do not treat that
as a complete release: all three receipts for the selected SHA must be present
and verified. No new release coordinator or staging apply is installed here.

`meet-agents` is built from the closed `src/agents` context with
`src/agents/Dockerfile`, the `production` target and an unprivileged
`DOCKER_USER`. Publication does not update Helm values or Kubernetes. A later,
serialized staging change must set `agentSubtitles.image.repository` and
`agentSubtitles.image.digest` from the receipt, and provide the selected
provider credential through a Kubernetes `secretKeyRef` rather than a literal
value.

## Canary activation and rollback

Serialize every staging mutation with the Scaleway agent. Starting from the
published receipt, first record the current revision of the `meet` Helm
release as R0 (`helm history meet -n <namespace>`). The backend and the
subtitle agent share that release. Confirm with
`helm get values meet -n <namespace> --revision R0` that R0 has the previous
agent image, the previous STT provider and `ROOM_SUBTITLE_ENABLED` off, and
record that evidence with R0. Then apply the canary in this order:

1. Set `agentSubtitles.image.repository` and
   `agentSubtitles.image.digest` to the immutable image from the receipt.
2. Set the agent environment to `STT_PROVIDER=mistral-live`,
   `MISTRAL_STT_LANGUAGE=fr`, and the measured streaming delay.
3. Project `MISTRAL_ASR_API_KEY` into the agent through a Kubernetes
   `secretKeyRef`. Do not put its value in Helm values, Git, workflow inputs
   or the candidate receipt.
4. Deploy and verify the agent while `ROOM_SUBTITLE_ENABLED` remains off.
5. Enable `ROOM_SUBTITLE_ENABLED` last to admit live subtitle dispatches.

Rollback is ordered to stop new work before restoring the previous release:

1. Turn `ROOM_SUBTITLE_ENABLED` off.
2. Wait until active subtitle dispatches have been cleaned up.
3. Run `helm rollback meet R0 -n <namespace>` with the revision recorded
   before the canary. Never run `helm rollback meet` without a revision: the
   immediately previous revision is the canary itself, with subtitles still
   enabled.
4. Revert the staging values to the R0 state in the deployment source as
   well, otherwise the next `helmfile apply` reapplies the canary.
