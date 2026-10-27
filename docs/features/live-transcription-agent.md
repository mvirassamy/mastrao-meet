# Live transcription agent (POC)

This opt-in worker path uses LiveKit Agents 1.8.3 with one realtime provider
per subscribed audio track. It is isolated from the existing Deepgram/Kyutai
path and does not change the Meet audio/video media pipeline.

## Activation

The backend room subtitle flag remains OFF by default. The qualified French
path uses `STT_PROVIDER=mistral-live`, a secret-only `MISTRAL_ASR_API_KEY`,
`MISTRAL_STT_LANGUAGE=fr`, and optional comma-separated
`MISTRAL_STT_CONTEXT_BIAS` terms. The model is fixed to
`voxtral-mini-transcribe-realtime-3` and the default streaming delay is 480 ms.

The rollback path uses `STT_PROVIDER=openai-live`, a secret-only
`OPENAI_API_KEY`, and `OPENAI_STT_LANGUAGES=fr`. It keeps the fixed
`gpt-live-transcribe` model and requires Silero VAD.

## Transport contract

The worker creates one provider stream per subscribed remote audio track, so
two tracks from one participant stay distinct. Each `lk.transcription` text
stream contains a versioned envelope with participant identity, track SID,
leg, item ID, sequence, revision, interim/final state, language and optional
provider timing.
OpenAI interim and final events reuse the item ID and increase the revision;
the frontend reducer therefore replaces the interim segment instead of
appending a duplicate. Mistral deltas are append-only final segments and the
frontend joins them into the same speaker turn, including punctuation-only
deltas. Reconnects, track loss and provider failures publish a versioned gap
marker on `mastrao.transcription.gap.v1`.

The worker cancels provider, audio and lifecycle tasks on track removal,
participant departure, room reconnect and process shutdown. A room reconnect
fences the old track streams and resubscribes the currently subscribed tracks
after the room is connected again.

## Evidence boundary

Unit tests use fakes only to verify envelope ordering, identity preservation,
replacement semantics and shutdown. They are not evidence of a real provider
transcription. A real-room validation still requires a deployed immutable
image, secret references and a measured staging meeting.
