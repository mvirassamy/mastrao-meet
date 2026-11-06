# Live transcription contract

The room-scoped store consumes only the complete, versioned Mastrao envelope
on the `lk.transcription` text-stream topic.

With `livekit-client@2.20.0`, the SDK exposes no typed `lk.transcription`
handler. The supported integration point is:

- `room.registerTextStreamHandler('lk.transcription', callback)`;
- `reader.readAll()` for the stream payload;
- `reader.info.attributes` for string metadata.

The envelope must contain `participantIdentity`, `trackSid`, `legId`, `itemId`,
`sequence`, `revision`, `state`, text, and `receivedAt`. Incomplete or
unversioned payloads are rejected.

`participantIdentity` is authoritative because it was bound to the admitted
microphone before ASR. The stream is accepted only from the exact deterministic
Room Audio live-worker identity for this room. That sender proves publication
authority but never replaces the speaker identity. The LiveKit participant
lookup may help render a known display name, but it must never replace that identity.
There is no legacy event bridge, inferred sender identity, synthetic track, or
room-mix fallback.
