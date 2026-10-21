# Canonical live worker send control

This is an isolated, opt-in worker foundation. It is **not qualified for
activation or long meetings**. Existing jobs without `liveControl` retain their
current provider path. A malformed JSON job, present malformed `liveControl`,
missing configuration, or a canonical job routed to a provider other than
`openai-live` fails before room connection/provider construction at the worker
entrypoint. This does not change which provider existing jobs select.

## Dispatch and private transport

The agreed job metadata adds this field to its existing JSON object:

```json
{"liveControl":{"version":1,"url":"http://backend:8000/internal/v1/meetings/transcripts/live/control","capability":"opaque-Django-signed-service-capability"}}
```

Set `LIVE_TRANSCRIPT_CONTROL_URL` in the worker to that **exact URL** and
`STT_PROVIDER=openai-live`. The backend has its own corresponding configured
control URL. This document does not activate either configuration.

The URL accepts HTTP/HTTPS, private IPv4 ranges 10/8, 172.16/12, 192.168/16,
IPv6 fc00::/7, loopback, the exact hosts `backend`, `meet-backend`, `localhost`,
`host.docker.internal`, and names ending in `.internal` or `.svc.cluster.local`.
It rejects credentials, query strings, fragments and whitespace. Metadata
cannot select a different endpoint. DNS/network policy must keep configured
private names inside the private network.

Requests use only `Authorization: Bearer <capability>`; Django's `:` delimiters
and optional compression `.` are accepted. No participant cookie, bearer,
Core signing key or browser-controlled authority is forwarded. Capabilities
are omitted from client representations and never logged. aiohttp uses
`trust_env=False`, no cookies or redirects, a five-second total timeout, no
HTTP retry, and a 16 KiB response limit. Compressed responses are refused.

Commands contain exactly `version`, `action`, `room_sid`, `participant_sid`,
`track_sid`, `attempt_ref`, `worker_instance_ref`, plus `source_ref` and
`admission_ref` for actions after `prepare`. References follow Core's
`[A-Za-z0-9_-]{16,160}`. The worker instance UUID is fresh per job invocation;
an attempt UUID is stable for one real room/participant/track SID tuple.
Microphone selection uses the RTC publication source and audio track kind,
never participant display names or attributes.

## Admission, continuation and stop

1. Reserve the epoch locally before any await that could permit a duplicate
   attempt, then call `prepare`. Before any claim, temporarily unavailable
   preparation (HTTP 404/502/503/504 or transport failure) may retry with the
   identical attempt: at most three requests, one second between attempts,
   within twelve seconds total. Strict/malformed responses and other refusals
   do not retry. This tolerates briefly delayed persisted webhooks; it cannot
   wait indefinitely for consent accepted after dispatch. No provider or RTC
   AudioStream is acquired during this wait.
2. Send one `claim`. Only its first response with both `start_authorized=true`
   and `send_authorized=true`, a non-replayed claim, an exact binding and a
   valid lease permits provider construction. A lost/refused/replayed claim
   never retries or opens. A renewal never opens a provider.
3. Renew approximately every five seconds. The deadline is monotonic, derived
   conservatively from Core's absolute Unix-second expiry, the local request
   elapsed time and wall-clock observations, bounded to fifteen seconds.
   Every `push_frame` rechecks authority, and an independent watchdog checks
   expiry even while renewal I/O or its cancellation cleanup is blocked.
4. Expiry, refusal, transport loss or malformed continuation response fences
   admission and the SDK socket synchronously. Queued appends/commits are
   discarded before reaching the socket; pending write tasks are cancelled.
   Bytes already handed to the network cannot be recalled. Close the SDK and
   join the existing local
   audio and ordered publication barriers. Do not flush queued audio or call
   `end_input`. Join control I/O after audio closes. Only successful local
   shutdown/delivery can send `stopped`; SDK close or publication failure
   prevents ACK. A received incoherent claim also forbids ACK; only a coherent
   receipt or an unknown transport outcome can justify exact-owner cleanup.
   A failed/ambiguous ACK is not silently retried.
5. Retain the local epoch tombstone for this job, including after reconnect,
   unsubscribe or failure. No second provider is opened under the same epoch.
   A later job still depends on Core's permanently sealed source ownership.

## SDK and quality limits: activation blocked

The installed/pinned `livekit-agents` and `livekit-plugins-openai` **1.8.3**
contain two independent retry mechanisms. `APIConnectOptions(max_retry=0)`
only disables the base STT retry loop. OpenAI `stt.py` also has a `while True`
connection loop (around line 962), and rotates its session after
`_max_session_duration = 10 * 60` (around lines 79 and 944).

`AuthorizedOpenAI` bounds a private SDK integration to this reviewed version:
its pool allows exactly one acquisition, including no socket reuse, and its
`_connect_ws` also seals the first connection attempt before I/O. A second
acquisition is refused before any connection. SDK version changes fail closed
until source review and the synthetic SDK execution tests are updated. The
model and language configuration remain `gpt-live-transcribe` and the existing
language hints; there is no alternate provider, ASR replay or fallback.

Consequently the SDK's ten-minute rotation **ends canonical transcription**
and publishes `canonical-provider-reconnect-refused`. Audio beyond that point
is not covered. This is a quality blocker for long meetings; neither the SDK
limit nor provider constraints are bypassed. A session error also leaves no
successful stopped ACK if local delivery failed. Core lease expiry can
request stop but does not itself certify a stopped worker.

The existing `sdk-close-final-drain-unconfirmed` gap remains: SDK close can
discard finals not yet yielded publicly. Previously accepted publications are
joined, but this proves neither durable Core transcript collection nor full
meeting coverage. `live-send-authority-lost` reports a control loss. After
bounded preparation exhaustion, the epoch stays closed and a provider-error
gap is emitted by the manager; late consent beyond that window requires a
separate qualified dispatch policy. Current fixtures establish control/stop
safety, **not transcription quality or complete summaries**.

LiveKit's subscription callbacks provide the track before preparation; this
worker acquires/reads no `rtc.AudioStream` and opens no provider until claim.
This is not a proof that the LiveKit RTC transport itself received no packets
before the callback. Transport-level subscription admission would require an
additional integration change.

## Offline validation

From the repository root:

```sh
PYTHONPATH=src/agents src/agents/.venv/bin/python -m unittest discover -s src/agents/tests -q
src/agents/.venv/bin/ruff check src/agents
src/agents/.venv/bin/ruff format --check src/agents/live_send_control.py src/agents/live_single_connection.py src/agents/live_transcriber.py src/agents/multi_user_transcriber.py src/agents/tests/test_live_control_entrypoint.py src/agents/tests/test_live_send_control.py src/agents/tests/test_live_send_sessions.py src/agents/tests/test_live_single_connection.py
```

The fixtures exercise real ephemeral loopback HTTP, the real worker entrypoint,
and the pinned SDK's actual retry/rotation loop with synthetic websockets.
They use no provider API, real audio, production environment or secret.
