# Live transcription contract

The room-scoped store consumes the `lk.transcription` text-stream topic and
keeps `RoomEvent.TranscriptionReceived` as a legacy compatibility bridge.

With `livekit-client@2.20.0`, the SDK exposes no typed `lk.transcription`
handler. The supported integration point is:

- `room.registerTextStreamHandler('lk.transcription', callback)`;
- `reader.readAll()` for the stream payload;
- `reader.info.attributes` for string metadata.

The standard LiveKit transcription attributes available to this version are:

- `lk.segment_id`;
- `lk.transcribed_track_id`;
- `lk.transcription_final`.

The versioned Mastrao envelope adds `participantIdentity`, `trackSid`, `legId`,
`itemId`, `sequence`, `revision`, `state`, and `receivedAt`. It is required for
complete multi-track ordering and reconnect semantics.

Fallback behavior is explicit:

- missing `participantIdentity` falls back to the stream sender identity;
- missing `trackSid` falls back to `unknown-track`;
- missing `legId` falls back to `legacy`;
- missing `itemId` falls back to the text stream id;
- missing `sequence` and `revision` are allocated by the room reducer;
- legacy `RoomEvent.TranscriptionReceived` segments use the participant and
  publication supplied by LiveKit, with the same `legacy` leg.

Fallback segments remain provider-independent, but they cannot guarantee
cross-reconnect ordering. Publishers should migrate to the versioned envelope
before the live worker is enabled for production.
