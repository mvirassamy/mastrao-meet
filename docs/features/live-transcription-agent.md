# Live transcription agent (POC)

This opt-in worker path uses LiveKit Agents 1.8.3 and exactly OpenAI's
`gpt-live-transcribe` model. It is isolated from the existing Deepgram/Kyutai
path and does not change the Meet audio/video media pipeline.

## Activation

The backend room subtitle flag remains OFF by default. To activate the POC in
an explicitly configured environment, set `STT_PROVIDER=openai-live`, provide
`OPENAI_API_KEY`, and use `OPENAI_STT_LANGUAGES=fr,en` for French/English code
switching. The model name is deliberately not configurable. The worker forces
Silero VAD because `gpt-live-transcribe` does not provide server-side turn
detection.

## Transport contract

The worker creates one provider stream per subscribed remote audio track, so
two tracks from one participant stay distinct. Each `lk.transcription` text
stream contains a versioned envelope with participant identity, track SID,
leg, item ID, sequence, revision, interim/final state, language and optional
provider timing.
Interim and final events reuse the item ID and increase the revision; the
frontend reducer therefore replaces the interim segment instead of appending a
duplicate. Reconnects, track loss and provider failures publish a versioned
gap marker on `mastrao.transcription.gap.v1`.

The worker cancels provider, audio and lifecycle tasks on track removal,
participant departure, room reconnect and process shutdown. LiveKit/OpenAI
provider reconnects remain inside the SDK stream; a room reconnect fences the
old track streams and resubscribes the currently subscribed tracks after the
room is connected again.

## Evidence boundary

Unit tests use fakes only to verify envelope ordering, identity preservation,
replacement semantics and shutdown. They are not evidence of a real OpenAI
transcription. A real-room validation still requires a local LiveKit room and
an operator-provided OpenAI key; no staging deployment is part of this POC.
