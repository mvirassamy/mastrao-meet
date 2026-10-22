# Controlled live microphone transcription

This path connects server RTC facts to the canonical Core live authority.
It is developed on `develop_modes`; it does not activate a mode, deploy a
worker or change the engines. Core remains the owner of consent and sources.

## Boundaries

Meet derives the binding from `MastraoRtcTrackEpoch`, its connection, the
issued media-token binding and the host or guest grant. Only an authenticated,
correlated microphone audio publication can prepare a source. Participant
attributes, display names and browser-provided consent references are not
admission evidence.

Core resolves the current consent for that exact participant session. A
prepared source is a journal, not permission to open the provider. The worker
must obtain a first `claim` with both `start_authorized` and `send_authorized`.
The replay of a claim cannot recover an opening permission lost in transport.
A `renew` extends continuation only. It cannot open or restart a provider.

## Worker protocol

Server-created job metadata adds a `liveControl` object containing version 2,
a private endpoint URL and an opaque technical capability. It contains no
participant bearer, receipt-signing key or transcript. The worker accepts only
the explicitly configured endpoint and validates bounded responses.

Requests carry version, action (`prepare`, `claim`, `renew`, `rotate`,
`stopped`, `deliver`), actual room/participant/track SIDs, a stable attempt reference and a
worker-instance reference. All actions except prepare also carry source,
admission and leg refs. Only rotate adds the successor leg and cumulative PCM
end position. Deliver adds a version 2 final, gap or end event; its source and
leg must match the command. It returns a durable receipt, never send authority.
The capability is scoped to the room and subtitle-control generation.

Prepare returns version 2, source, admission and the initial leg reference. The
send operations return Core's exact send-authority response. Meet signs the
Core calls and never mirrors consent decisions. Lost or ambiguous responses
fail closed; no automatic audio repair or replay starts.

## Stop and quality limits

The worker fences frame delivery at the lease deadline independently from
network renewal. It also checks at each SDK socket write, drops queued
appends/commits after expiry and cancels writes awaiting I/O. Bytes already
handed to the network cannot be recalled. Refusal, expiry or control loss closes
the local stream.
Consent withdrawal is observed through renewal; this polling path does not
promise instantaneous remote revocation. Its remaining permission is bounded
by the existing lease, never extended locally after a failed renewal.
Only successful provider cleanup permits a `stopped` acknowledgement. Slow
subtitle publication is not proof of a still-open audio stream; accepted SDK
results use the bounded delivery queue already implemented.

The source cannot be reopened after expiry, stop or an unexpected
reconnection. This can create an explicit incomplete transcript. Planned
handoffs drain the SDK and accepted publications before opening a successor.
Final events use the existing Core journal and verified private objects before
publication succeeds. A receipt confirms the exact digest and sequence; it
does not certify exhaustive SDK capture, a common meeting clock, complete
coverage or automatic summary eligibility. Tests use synthetic media and
transports only.

The pinned OpenAI plugin rotates its realtime connection after ten minutes.
Each connection still refuses a second acquisition. Planned continuation uses
a separate Core-authorized leg after the old connection drains and closes;
see [the continuation boundary](mastrao-live-continuation.md). An unexpected SDK
rotation remains terminal. Do not remove the SDK limit or silently reconnect
to hide a gap. This inactive path still requires real quality qualification.

## Configuration boundaries

`MASTRAO_LIVE_WORKER_CONTROL_ENABLED` defaults to false. The backend control URL,
Core prepare, send and delivery endpoints default to empty strings. The new
`MASTRAO_CORE_LIVE_DELIVERY_ENDPOINT` must point exactly to
`/internal/v1/meetings/transcripts/live/append` on the private Core. The
worker must have an exact matching `LIVE_TRANSCRIPT_CONTROL_URL`. This document
does not enable or supply any of those values. The control endpoint belongs to
`/internal/v1/meetings/transcripts/live/control`, outside public API routing.

The host handoff stores the organization reference from its verified grant as
a technical binding. Older grants without that binding must be renewed through
the ordinary trusted handoff; they must not borrow recording metadata. Live
transcription does not require a recording policy or recording-session visit.

## Rollback prerequisite

Before reverting an eventual deployment, stop controlled dispatch intents and
await worker cleanup. Do not replace a controlled worker with an older binary
that would ignore its metadata and use the legacy provider path. Disabling
the control gateway prevents renewal and bounds remaining audio by the lease;
it does not itself prove that a stopped acknowledgement was received. The
nullable organization binding migration can remain in place while code is
rolled back; it does not rewrite historical grants.
