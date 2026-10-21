# Planned live connection continuation

This disabled foundation extends controlled live transcription on
`develop_modes`. It preserves the current model, language hints and one logical
live ASR pass per microphone epoch. It does not activate a mode or deploy a
migration, endpoint or worker.

## One source, disjoint connection legs

The source and attempt remain bound to the exact RTC room, participant and
microphone track. Each provider connection has its own canonical `leg_ref`.
Core owns the leg journal and the existing source lease. Meet stores no consent
or send-state mirror. A leg is an identifiable provider invocation, not a new
pass over the same audio.

The worker plans rotation at 540 seconds, before the pinned SDK's 600-second
limit. It retains one RTC reader and queues only newly captured, unsent PCM in
a bounded volatile buffer. The old connection must drain its admitted PCM,
commit the remaining tail, receive all expected acknowledgements and finals,
finish publication and close successfully before requesting the next leg.
During this drain the old lease must remain valid; loss of authority fences
writes immediately without flushing. Failures remain explicit and terminal.

Core accepts `rotate` only for the exact active owner and leg, with a live lease,
current consent and resource authority, at least 480 seconds after its claim.
The transition closes the predecessor's interval and reserves a distinct
successor atomically. Its first response may authorize one opening. A replay,
renewal, lost response or terminal stop cannot authorize another opening.
The new socket's permission captures its leg identity, so an old socket cannot
become authorized again when the current leg changes.

## Strict version 2 wire

`liveControl` metadata and worker commands use version 2 exclusively. Core
preparation/admission remain version 1; the gateway projects their verified
initial `leg_ref` in its version 2 prepare response. Core send uses version 2,
with the same private path, signature purpose and binding fields as before.

Every command after prepare adds `leg_ref`. Only rotate also accepts
`next_leg_ref` and `audio_end_sample`. The latter is an exclusive cumulative
sample position on this 24,000 Hz mono PCM input, represented by a nonnegative
safe integer. Core send responses also bind `leg_ref` and `audio_start_sample`.
For rotate these must equal the requested successor and predecessor end.
Claim starts at zero; renewal cannot grant a new opening.

An incoherent response, old protocol, changed local RTC/control authority or
invalid boundary is refused without passing an opening permission to the
worker. The gateway rechecks local authority after Core I/O, including rotate.

## Evidence and qualification limits

The initial SDK analysis ran real pinned loops against synthetic sockets:
7,200 numbered samples crossed two separate connections exactly once, and a
blocked publication prevented close. That diagnostic is distinct from the
persistent complete worker/authority tests required for this feature.

Sample positions in Core are authenticated worker assertions, not proof of
provider billing, semantic transcript quality, meeting-wide coverage or remote
provider termination. Local websocket ACK/final observations establish only the
reported transport boundary. The complete durable Core final publisher and
summary source qualification remain separate work. No billed savings or real
ASR quality result is claimed by synthetic tests.

An unexpected SDK reconnect, missing ACK/final, drain timeout, queue overflow,
failed publication/close or control loss leaves an explicit incomplete outcome.
There is no overlap window, replay buffer, ASR fallback or automatic repair.
Normal shutdown and consent withdrawal keep their immediate no-flush fence.
Existing capability and participant-grant expiry limits still apply to long
meetings; continuation does not renew or bypass these credentials.

## Future rollout and rollback boundary

The Core shared-schema migration is additive and must follow its reviewed
operator procedure. Drain existing controlled version 1 jobs before an eventual
version 2 deployment; they cannot be adopted by the new dispatch reconciler.
No fabricated leg history is backfilled for old active sources. Jobs without
`liveControl` keep their existing product path.

Stop controlled dispatches and wait for successful cleanup before reverting
binaries. Do not replace a controlled worker with one that ignores its metadata.
Keeping the additive journal in place is safer than removing runtime history.
No rollout, rollback or configuration mutation is performed by this change.
