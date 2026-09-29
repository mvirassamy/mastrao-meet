# Staging candidate publication

The `Publish one Meet staging candidate` workflow
(`.github/workflows/staging-candidate.yml`) builds exactly one Meet image,
`meet-frontend`, `meet-backend` or `meet-agents`, from a full commit SHA
reachable from `develop`, pushes it to the Scaleway staging registry and
uploads a digest-pinned receipt. It never deploys: every receipt records
`PUBLISHED_NOT_DEPLOYED` and `deploymentApplied: false`.

## What the workflow proves

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
- The registry readback hashes the raw index returned for
  `repository@digest` and rejects it unless the bytes match the digest, the
  index holds exactly one `linux/amd64` image, and an attestation manifest
  references that image.

## Activation prerequisites

The job-level `if: github.ref == 'refs/heads/develop'` only protects the
copy of the workflow stored on `develop`. A workflow edited on another
branch could request the same environment, so the credential boundary must be
enforced by GitHub itself. In this order:

1. Create the `staging-candidates` environment with:
   - deployment branches restricted to `develop` only;
   - at least one required reviewer, with "Prevent self-review" enabled.

   GitHub creates a missing environment **without** protection rules the
   first time a job references it, so do this before step 2 and check the
   rules again if the environment already exists.
2. Store `MEET_STAGING_REGISTRY_PASSWORD` **only** as a secret of that
   environment. Never define it as a repository or organisation secret: the
   workflow cannot tell where the secret came from.
3. Make the workflow dispatchable: GitHub only offers `workflow_dispatch` for
   workflows present on the default branch (currently `main`), so either
   switch the default branch to `develop` or sync this workflow to `main`.

The "Require the dedicated registry publisher" step only fails closed while
no `MEET_STAGING_REGISTRY_PASSWORD` is visible to the job. It does not check
reviewers, branch rules or where the secret is stored: steps 1 and 2 are the
actual security boundary.

## Running it

Dispatch the workflow from `develop` with the target and the full source
SHA, then download the `meet-<target>-<sha>-candidate-receipt` artifact and
promote the image only by its `image.reference` (`repository@digest`).

`meet-agents` is built from the closed `src/agents` context with
`src/agents/Dockerfile`, the `production` target and an unprivileged
`DOCKER_USER`. Publication does not update Helm values or Kubernetes. A later,
serialized staging change must set `agentSubtitles.image.repository` and
`agentSubtitles.image.digest` from the receipt, and provide `OPENAI_API_KEY`
through a Kubernetes `secretKeyRef` rather than a literal value.

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
2. Set the agent environment to `STT_PROVIDER=openai-live` and
   `OPENAI_STT_LANGUAGES=fr,en`.
3. Project `OPENAI_API_KEY` into the agent through a Kubernetes
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
