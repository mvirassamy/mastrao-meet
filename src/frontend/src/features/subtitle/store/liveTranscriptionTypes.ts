export const LIVE_TRANSCRIPTION_TOPIC = 'lk.transcription'
// Versioned Mastrao topic on which the live worker publishes gap markers.
export const LIVE_TRANSCRIPTION_GAP_TOPIC = 'mastrao.transcription.gap.v1'
// Backend-only state transitions are deliberately separate from transcript data.
export const LIVE_TRANSCRIPTION_STATE_TOPIC = 'mastrao.transcription.state.v1'
export const LIVE_TRANSCRIPTION_SCHEMA_VERSION = 1
export const MAX_FINAL_TRANSCRIPTION_SEGMENTS = 5_000
export const MAX_ACTIVE_TRANSCRIPTION_SEGMENTS = 256

export type LiveTranscriptionStatus =
  | 'unknown'
  | 'inactive'
  | 'starting'
  | 'live'
  | 'reconnecting'
  | 'degraded'
  | 'unavailable'
  | 'stopping'
  | 'stopped'

export type LiveTranscriptionConnectionStatus =
  | 'connected'
  | 'reconnecting'
  | 'disconnected'

export type LiveTranscriptionSegmentState = 'interim' | 'final'
export type LiveTranscriptionResyncStatus = 'idle' | 'pending' | 'failed'
export type LiveTranscriptionStartStatus =
  | 'idle'
  | 'pending'
  | 'error'
  | 'success'
export type LiveTranscriptionMetadataSource =
  | 'envelope'
  | 'livekit-attributes'
  | 'legacy-event'

export interface LiveTranscriptionSegmentInput {
  participantIdentity: string
  trackSid: string
  legId: string
  itemId: string
  state: LiveTranscriptionSegmentState
  text: string
  sequence?: number
  revision?: number
  language?: string
  startTime?: number
  endTime?: number
  receivedAt?: number
  metadataSource?: LiveTranscriptionMetadataSource
}

export interface LiveTranscriptionSegment extends LiveTranscriptionSegmentInput {
  key: string
  sequence: number
  revision: number
  receivedAt: number
  metadataSource: LiveTranscriptionMetadataSource
}

export interface LiveTranscriptionGap {
  id: string
  reason: string
  fromSequence?: number
  toSequence?: number
  receivedAt: number
}

export type LiveTranscriptionTransportEvent =
  | { type: 'segments'; segments: LiveTranscriptionSegmentInput[] }
  | { type: 'gap'; gap: LiveTranscriptionGap }

export type LiveTranscriptionStatePacket = {
  schemaVersion: typeof LIVE_TRANSCRIPTION_SCHEMA_VERSION
  roomSid: string
  state: LiveTranscriptionStatus
  stateVersion: number
  eventId: string
  occurredAt: string
  sessionId?: string | null
  reason?: string | null
}

export type LiveTranscriptionStateSnapshot = {
  roomSid: string | null
  state: LiveTranscriptionStatus
  stateVersion: number
  sessionId: string | null
  occurredAt: string | null
  reason: string | null
}

export interface LiveTranscriptionTextStreamReader {
  info: {
    id: string
    attributes?: Record<string, string>
  }
  readAll: () => Promise<string>
}

export interface LiveTranscriptionState {
  roomId: string
  roomSid: string | null
  status: LiveTranscriptionStatus
  stateVersion: number | null
  stateEventId: string | null
  stateOccurredAt: string | null
  stateReason: string | null
  resyncStatus: LiveTranscriptionResyncStatus
  resyncRequestId: number
  connectionStatus: LiveTranscriptionConnectionStatus
  segments: LiveTranscriptionSegment[]
  gaps: LiveTranscriptionGap[]
  truncated: boolean
  nextSequence: number
}

export type LiveTranscriptionAction =
  | { type: 'reset'; roomId: string }
  | { type: 'ingest'; event: LiveTranscriptionTransportEvent }
  | { type: 'state-packet'; packet: LiveTranscriptionStatePacket }
  | { type: 'snapshot'; snapshot: LiveTranscriptionStateSnapshot }
  | { type: 'request-resync' }
  | { type: 'resync-failed' }
  | { type: 'status'; status: LiveTranscriptionStatus }
  | { type: 'connection'; status: LiveTranscriptionConnectionStatus }
  | { type: 'gap'; gap: LiveTranscriptionGap }
