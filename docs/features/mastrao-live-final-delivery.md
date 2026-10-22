# Durable controlled live finals

Controlled workers deliver final transcript events through the existing private
Meet control endpoint. Interim subtitles stay volatile in LiveKit. Core owns
canonical metadata, consent and authorization; private object storage owns the
verified event bytes. Django stores no second transcript or business state.

## Wire and ownership

`deliver` uses the existing room-scoped technical capability and the exact RTC
room, participant, microphone track, source, admission, attempt, worker and leg
binding. Meet resolves recorded server RTC facts, signs a purpose-specific v2
assertion containing the event digest, and forwards `{request,event}` to
`/internal/v1/meetings/transcripts/live/append`. Participant display names,
attributes, browser consent claims and caller-supplied organization are not
trusted facts. The event contains v2, source, leg, per-source sequence and one
final, gap or terminal marker. Exact provider text is retained, including
whitespace and Unicode; canonical bytes use shared schema field order.

Core checks the existing source and connection owner at both reservation and
confirmation, with current tenant/resource permission, consent and retention.
A successful receipt follows the verified private write and confirmation.
Different bytes at an occupied sequence conflict. Repeating the exact event
cannot create another stored final. Provider item identities are namespaced by
connection, so equal item IDs on successive sockets remain distinct.

## Worker and failure boundary

The per-track journal sequence continues across authorized provider rotations.
Successful final publication waits for the exact Core digest and sequence
receipt. Rotation therefore waits for durable finals as well as SDK drain and
physical connection closure. Delivery does not renew audio authority. There is
no automatic delivery retry, audio replay, provider replacement or second ASR
pass after a refused, lost or malformed receipt. Such uncertainty is terminal.

A final accepted before local RTC closure may still be delivered using its
recorded binding. Changing the subtitle control generation refuses the old
capability. Consent withdrawal, expiry or other Core authority changes refuse
new durable writes. An undelivered terminal marker leaves the source collecting;
a failed delivery never becomes a successful stopped acknowledgement.
Technical logs must not contain transcript text or capabilities; request event
payloads are filtered from error reporting.

## Qualification and synthesis

The pinned SDK does not attest a common meeting clock. Controlled finals carry
the enclosing committed input buffer as `timing: {state: "source_buffer",
sample_rate: 24000, start_sample, end_sample}`. The worker derives these exclusive
bounds from successful PCM writes, issued commits and acknowledged item order;
source offsets continue across legs. These are buffer bounds, not precise speech
or word timing. A final arriving before its ACK waits for that evidence within
a bounded timeout. If timing evidence remains unavailable, retain the received
final and its exact text with unavailable timing. This does not clear any
terminal transport error, bypass drain, authorize rotation or claim completeness.

The gateway orders the four timing fields before hashing; Core checks the buffer
against the recorded leg at both reservation and confirmation. Unlocated events
retain `timing: {state: "unavailable"}`. The journal keeps sticky incomplete state
and `meeting_coverage: "unverified"` for either timing state. Receive time and
cumulative PCM position cannot establish meeting-wide coverage.

This slice persists the finals. It does not assemble/select an eligible V2
meeting transcript or wire the journal into automatic summaries. That next slice
must establish the expected track roster and justified common audio clock,
verify current object bytes and authority, then extend the existing Meeting
Brief source lifecycle coherently. A durable event acknowledgement must never
be used as proof that a whole-meeting summary is complete. No missing source
triggers an implicit replacement ASR pass.

## Configuration and rollout

The controlled path remains disabled by default. The new backend setting
`MASTRAO_CORE_LIVE_DELIVERY_ENDPOINT` defaults to empty and must use the exact
private append path above. It is validated before controlled job dispatch.
No environment value is supplied or activated by this change.

Before any future deployment, drain older controlled v2 jobs: they share the
control protocol number, but earlier generations either lack durable finals or
do not retain committed source-buffer evidence. Deploy matching
Core, gateway and worker code together. Rollback first stops controlled dispatch
and awaits cleanup; do not adopt an older worker or promote partial journals
as complete transcripts. Existing additive journal migrations remain intact.
