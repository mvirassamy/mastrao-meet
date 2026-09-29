# Subtitle agent convergence (Backend B)

This document describes the isolated subtitle-agent convergence path. It does
not change the Meet audio/video media pipeline and it does not claim that a
provider performed a real transcription.

## Rollout

`ROOM_SUBTITLE_CONVERGENCE_ENABLED` is `false` by default. With the flag off,
ON keeps the legacy list/create behavior; OFF cleanup is still serialized and
always uses list-delete-list. Set the flag to `true` before a canary so the
robust path is active for the canary itself. The robust path uses one exact
LiveKit room SID, an exact dispatch metadata contract, a permanent PostgreSQL
room-key registry, a process gate, and a dedicated PostgreSQL session advisory
lock.

The uniqueness guarantee applies only while the flag is on and only for
dispatches carrying the exact room, agent, provider, generation (and any
deployment) metadata. Legacy unannotated dispatches are adopted when they are
unambiguous; malformed metadata is treated as unsafe and remains visible as an
ambiguous state.

## Rollback and operations

To roll back the robust path, set
`ROOM_SUBTITLE_CONVERGENCE_ENABLED=false` and continue draining OFF intents.
The registry is append-only: room SIDs never reuse advisory keys, and the
namespace is configurable but must remain stable for a deployment. Lock
contention is bounded; Celery is only a wakeup/retry mechanism and never the
mutual exclusion primitive. Synchronous callers wait only up to
`ROOM_SUBTITLE_SYNC_BUDGET_SECONDS`, then return the durable pending state.

Room-finished events persist a closure fence before scheduling cleanup. A late
participant or start intent cannot reactivate a closed room. Provider calls are
never made while Django row locks are held; state intent and provider
convergence are separate transactions/phases.
