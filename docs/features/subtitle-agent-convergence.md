# Subtitle agent convergence (Backend B)

This document describes the isolated subtitle-agent convergence path. It does
not change the Meet audio/video media pipeline and it does not claim that a
provider performed a real transcription.

## Convergence

Every ON and OFF convergence runs under one room-scoped PostgreSQL session
advisory lock. It uses one exact LiveKit room SID, an exact dispatch metadata
contract, a permanent PostgreSQL room-key registry and a process gate. OFF
cleanup always uses list-delete-list.

The uniqueness guarantee applies to dispatches carrying the exact room, agent,
provider, generation (and any deployment) metadata. Legacy unannotated
dispatches are adopted when they are unambiguous; malformed metadata is
treated as unsafe and remains visible as an ambiguous state.

## Operations

Reconciliation and status-packet retries run on the `meet-backend` queue,
not on the serial `mastrao-transcription` ASR worker, so a stop or a cleanup
never waits behind a long transcription. To stop a provider, use the
`subtitles_kill_switch` command and keep draining OFF intents.
The registry is append-only: room SIDs never reuse advisory keys, and the
namespace is configurable but must remain stable for a deployment. Lock
contention is bounded; Celery is only a wakeup/retry mechanism and never the
mutual exclusion primitive. Synchronous callers wait only up to
`ROOM_SUBTITLE_SYNC_BUDGET_SECONDS`, then return the durable pending state.

Room-finished events persist a closure fence before scheduling cleanup. A late
participant or start intent cannot reactivate a closed room. Provider calls are
never made while Django row locks are held; state intent and provider
convergence are separate transactions/phases.
