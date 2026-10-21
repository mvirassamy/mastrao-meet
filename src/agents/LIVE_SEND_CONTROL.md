# Canonical live worker send control

This opt-in worker path is **not qualified for activation or live transcription
quality**. Jobs without `liveControl` keep their existing provider path. Present
but malformed control metadata, unconfigured endpoints, and a controlled job
routed to a provider other than `openai-live` fail before any provider or room
connection. Controlled version 1 jobs are rejected; there is no v1 fallback.

## Dispatch and private transport

The job metadata contains:

```json
{"liveControl":{"version":2,"url":"http://backend:8000/internal/v1/meetings/transcripts/live/control","capability":"opaque-Django-signed-service-capability"}}
```

Configure `LIVE_TRANSCRIPT_CONTROL_URL` to that exact URL and
`STT_PROVIDER=openai-live`. This document does not activate configuration.
The endpoint path remains `/internal/v1/...`; the payload protocol is v2.
Core's separate preparation/admission protocol remains v1 behind the gateway.

The URL permits HTTP/HTTPS on explicit private IPs/names: IPv4 10/8, 172.16/12,
192.168/16, IPv6 fc00::/7, loopback, `backend`, `meet-backend`, `localhost`,
`host.docker.internal`, and `.internal` or `.svc.cluster.local` names. Credentials,
query strings, fragments, whitespace and metadata-selected alternate URLs are
rejected. Deployment DNS/network policy must keep those names private.

Requests use `Authorization: Bearer <capability>` only. Django signing token
colons and compression prefixes are accepted; capabilities are omitted from
representations and logs. No participant credentials or Core signing key are
forwarded. aiohttp uses `trust_env=False`, no cookies/redirects, five seconds
maximum per request, no HTTP retry, 16 KiB maximum response, and no compressed
responses.

## Wire v2 and immutable leg identities

All commands contain `version=2`, `action`, `room_sid`, `participant_sid`,
`track_sid`, `attempt_ref`, and `worker_instance_ref`. `prepare` contains only
those fields. Every subsequent command additionally contains `source_ref`,
`admission_ref`, and `leg_ref`. Only `rotate` additionally sends `next_leg_ref`
and `audio_end_sample`. References match `[A-Za-z0-9_-]{16,160}`. Sample positions
are JSON-safe nonnegative integers at fixed **24,000-Hz mono int16 PCM**.

Gateway prepare returns exactly `version=2`, `source_ref`, `admission_ref`, and
`leg_ref`. The initial leg is a server fact, never guessed by the worker.
Send receipts contain exactly the existing source/owner/action/state/lease/flag
fields plus `version=2`, `leg_ref`, and `audio_start_sample`. Initial claim must
start at zero. A rotate receipt names `next_leg_ref` and must start at exactly
the preceding leg's `audio_end_sample`. Renew/stopped receipts preserve the
current leg's start. Every receipt validates exact field sets, primitive types,
identities, flags and conservative lease bounds.

One attempt UUID identifies one real RTC room/participant/track SID epoch. The
worker UUID is fresh per job. Microphones use RTC publication source and audio
kind, not display names/attributes. Epoch tombstones persist through failure,
unsubscribe and room reconnect; planned continuation does not acquire another
source or RTC attempt.

## Initial authorization and lease

1. Before claim, unavailable preparation may retry the identical attempt at
   most three times, one second apart, within twelve seconds. Strict/malformed
   replies do not retry. No provider or RTC AudioStream is acquired meanwhile.
2. Issue claim once. Only an exact unreplayed receipt with start/send authority
   and a valid lease permits construction. Lost/refused/replayed claim never
   retries or opens. A coherent receipt or transport-unknown claim can permit
   exact-owner cleanup; an incoherent received receipt cannot.
3. Renew approximately every five seconds. The deadline is monotonic, derived
   conservatively from Core's absolute Unix expiry, request elapsed time and
   wall-clock observations, bounded to fifteen seconds. Renew and rotate share
   one I/O lock. The expiry watchdog never waits for that lock or control I/O.
   Every PCM admission and actual socket write rechecks authority. Each socket
   captures its immutable leg: moving to a new leg never revives the old guard.

## Planned 540-second continuation

`live_continuation.py` retains **one RTC reader** across legs. Each leg has a
fresh `AuthorizedOpenAI`, SDK speech stream, item revision scope and Core
leg identity. The existing model `gpt-live-transcribe`, VAD and language hints
are preserved. A planned timer starts at each successful authorization and
fires at 540 seconds, ahead of the SDK's 600-second internal rotation; Core
independently enforces its minimum leg age of 480 seconds.

The handoff order is:

1. Seal old-leg admission at an exclusive cumulative PCM sample K. Subsequent
   captured frames are new, unsent PCM in a volatile queue bounded to ten
   seconds (240,000 samples) and 512 frames. No old sample is copied/replayed.
   Active-leg admission also bounds outstanding SDK PCM to 240,000 samples,
   measured against successful socket append progress; stalled writes fail
   explicitly before unbounded SDK input can accumulate.
2. While the old lease remains valid, call `seal_input()` once. Under the socket
   write lock, suppress SDK VAD commits after sealing, including commits already
   waiting for the lock. Recheck permission after acquiring it. SDK end_input
   flushes queued PCM and the partial 50-ms append tail; it leaves receive alive.
   It does not prove or generate a final commit. Silero EOF does not synthesize
   an END_OF_SPEECH event.
3. `drain_input()` counts actual successful append bytes divided by two, not
   SDK recognition usage or VAD sample clocks. After all admitted samples have
   reached the socket, send one explicit nonempty tail commit if needed. Do not
   add silence, replay bytes or send a duplicate empty commit. Drain has a
   retained shared outcome and a 25-second timeout.
4. Observe raw `input_audio_buffer.committed` ACKs from connection start. Track
   commit counts, unique item identities, predecessor links and completed items.
   ACK server event_id is not an echoed client commit ID. Wait for every ACK
   and all terminal items, regardless of final arrival order. A last-item final
   is insufficient. Empty completed transcripts resolve raw drain without
   inventing a public SDK final. Raw errors/failed transcriptions are terminal.
5. Wait for every nonempty final to traverse the real SDK iterator and finish
   ordered publication. A raw completion or a queued publication is insufficient.
   Then join SDK stream close, reader/publication completion, and actual socket
   close. The SDK pool swallows close callback exceptions; the authorized wrapper
   retains and explicitly rejoins the physical close result so failures cannot
   authorize another leg. Drain/publication/close have a thirty-second handoff
   budget; cancellation still joins already-started local close work.
6. Only after those barriers, issue Core rotate once, naming the old leg, a fresh
   successor, and K. Core alone reserves the next leg. Only an unreplayed
   start-authorized receipt with exact new identity/offset permits a new provider.
   No old/new physical sockets overlap. Send the queued new PCM starting at K.

A malformed receipt, changed sample offset, queue overflow, failed publication,
missing ACK/final, timeout, close failure, provider error or unknown rotate outcome
is terminal. There is no silent reconnect, audio catch-up, provider retry or new
opening on replay. Lost rotate can retain the exact candidate only for cleanup;
it cannot be retried as a start. No rotate is sent after an incomplete local drain.

## Hard stop and SDK pin

Expiry, withdrawal, control loss, unsubscribe and explicit stop synchronously
fence admission/socket writes and cancel pending senders before cleanup. These
paths **never flush or call end_input**. Join SDK close, the cancelled RTC reader,
and accepted ordered publications. Control cancellation/finalizers cannot delay
initial audio fencing. Concurrent stop callers retain the same close/delivery
outcome. Successful cleanup can acknowledge stopped; failed SDK close/publication
cannot. Previously handed-off network bytes cannot be recalled.

`AuthorizedOpenAI` requires `livekit-agents` and `livekit-plugins-openai` 1.8.3.
It preserves both `max_retry=0` and a single-use connection pool, plus a sealed
first connection attempt. The SDK's own while-True rotation remains forbidden
for each leg. Unexpected internal rotation still emits
`canonical-provider-reconnect-refused` and terminates; planned continuation
never weakens this guard. SDK changes require source review and new execution
proof before the pin can change.

## Evidence and qualification limits

Persistent tests execute the installed SDK send/VAD/receive/retry machinery with
synthetic websockets and numbered PCM. The full session fixture crosses three
accelerated 540-second timers: four authorized provider instances, 11,520 samples
sent exactly once, old physical close before successor construction, and repeated
provider item IDs distinguished by Core leg. Separate gates prove held append,
ACK/final, publication and socket close cannot be overtaken by Core rotate.
Tests cover empty/failed finals, absent ACK, timeout, queue saturation, lost
rotation, invalid identities/offsets, delayed renewal and cancellation, while
legacy routing and hard-stop tests remain in force. Networking is forbidden in
new SDK/session fixtures; no provider requests or environment files are needed.

Expected error-path SDK logs and gap diagnostics are synthetic test evidence,
not production failures or claims about transcription quality. A hard stop may
still emit `sdk-close-final-drain-unconfirmed`: SDK immediate close can discard
unread finals. Planned successful drain has a stronger local publication barrier.
Neither path proves durable Core collection, real provider ACK behavior, semantic
quality, full meeting coverage, complete summaries, or activation readiness.
LiveKit transport may have received packets before its subscription callback;
claim gating proves only the worker's AudioStream/provider acquisition boundary.

The [official commit reference](https://developers.openai.com/api/reference/resources/realtime/client-events#input_audio_buffer.commit)
checked on 2026-10-22 specifies an error for an empty buffer and a committed ACK;
it does not specify a numeric minimum for nonempty PCM. The installed OpenAI
types and LiveKit 1.8.3 sender likewise contain no minimum-duration guard. A
100-ms provider requirement is therefore **unverified**, not an asserted API
contract. Planned cuts commit any nonempty remainder, including 1–2,399 samples
after a recent VAD commit. Synthetic SDK tests cover 1 and 2,399 samples and
rejection, but do not establish real provider acceptance. Tiny-tail acceptance
remains an explicit activation qualification limit: rejection terminates the
source without rotate/retry, padding, replay or manufactured silence. No arbitrary
100-ms threshold is introduced from historical community reports.

## Offline validation

From the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src/agents src/agents/.venv/bin/python -m unittest discover -s src/agents/tests -q
src/agents/.venv/bin/ruff check src/agents
src/agents/.venv/bin/ruff format --check src/agents/live_send_control.py src/agents/live_single_connection.py src/agents/live_input_drain.py src/agents/live_continuation.py src/agents/live_transcriber.py src/agents/tests/test_live_control_entrypoint.py src/agents/tests/test_live_send_control.py src/agents/tests/test_live_send_sessions.py src/agents/tests/test_live_send_rotation.py src/agents/tests/live_continuation_fixtures.py src/agents/tests/test_live_input_drain.py src/agents/tests/test_live_continuation.py
```
