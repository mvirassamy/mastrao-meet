# Staging candidate publication

The `Publish one Meet staging candidate` workflow
(`.github/workflows/staging-candidate.yml`) builds exactly one Meet image,
`meet-frontend` or `meet-backend`, from a full commit SHA reachable from
`develop`, pushes it to the Scaleway staging registry and uploads a
digest-pinned receipt. It never deploys: every receipt records
`PUBLISHED_NOT_DEPLOYED` and `deploymentApplied: false`.

## What the workflow proves

- The source SHA is 40 lowercase hex characters, is the checked-out `HEAD`,
  is an ancestor of a freshly fetched `origin/develop` and leaves a clean
  worktree. Its Git tree and `git archive` SHA-256 are recorded.
- The build recipe (Dockerfile, target, repository) must equal the closed
  table in `scripts/ci/staging_candidate_receipt.py`; a unit test also keeps
  the workflow `case` block in sync with that table.
- The registry readback hashes the raw index returned for
  `repository@digest` and rejects it unless the bytes match the digest, the
  index holds exactly one `linux/amd64` image, and an attestation manifest
  references that image.

## Activation prerequisites

The job-level `if: github.ref == 'refs/heads/develop'` only protects the
copy of the workflow stored on `develop`. A workflow edited on another
branch could request the same environment, so the credential boundary must be
enforced by GitHub itself. Before storing any registry credential:

1. Create the `staging-candidates` environment with:
   - deployment branches restricted to `develop` only;
   - at least one required reviewer, with "Prevent self-review" enabled.
2. Store `MEET_STAGING_REGISTRY_PASSWORD` **only** as a secret of that
   environment. Never define it as a repository or organisation secret: the
   workflow cannot tell where the secret came from.
3. Make the workflow dispatchable: GitHub only offers `workflow_dispatch` for
   workflows present on the default branch (currently `main`), so either
   switch the default branch to `develop` or sync this workflow to `main`.

Until all three are done, the workflow fails closed at
"Require the dedicated registry publisher".

## Running it

Dispatch the workflow from `develop` with the target and the full source
SHA, then download the `meet-<target>-<sha>-candidate-receipt` artifact and
promote the image only by its `image.reference` (`repository@digest`).
