# Staging candidate publication

The `Publish one Meet staging candidate` workflow
(`.github/workflows/staging-candidate.yml`) builds exactly one Meet image,
`meet-frontend` or `meet-backend`, from a full commit SHA reachable from
`develop`, pushes it to the Scaleway staging registry and uploads a
digest-pinned receipt. It never deploys: every receipt records
`PUBLISHED_NOT_DEPLOYED` and `deploymentApplied: false`.

## What the workflow proves

- The verification tooling (`scripts/ci`) is checked out from the workflow's
  own commit into `ci-tools/`, never from the candidate source, and is
  imported before anything is pushed.
- The candidate source is checked out alone into `source/`. Its SHA is 40
  lowercase hex characters, is the checked-out `HEAD`, is an ancestor of a
  freshly fetched `origin/develop` and leaves a clean worktree. Its Git tree
  and `git archive` SHA-256 are recorded.
- The build recipe (Dockerfile, target, repository and every build argument)
  comes from the closed table in `scripts/ci/staging_candidate_recipe.py`.
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
