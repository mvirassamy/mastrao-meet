import type {
  Participant,
  TrackPublication,
  TranscriptionSegment,
} from 'livekit-client'

export const LIVE_TRANSCRIPTION_TOPIC = 'lk.transcription'
// Versioned Mastrao topic on which the live worker publishes gap markers.
export const LIVE_TRANSCRIPTION_GAP_TOPIC = 'mastrao.transcription.gap.v1'
export const LIVE_TRANSCRIPTION_SCHEMA_VERSION = 1
export const MAX_FINAL_TRANSCRIPTION_SEGMENTS = 5_000
export const MAX_ACTIVE_TRANSCRIPTION_SEGMENTS = 256

export type LiveTranscriptionStatus =
  | 'inactive'
  | 'starting'
  | 'live'
  | 'reconnecting'
  | 'degraded'
  | 'unavailable'
  | 'stopped'

export type LiveTranscriptionSegmentState = 'interim' | 'final'
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
  | { type: 'status'; status: LiveTranscriptionStatus }
  | { type: 'gap'; gap: LiveTranscriptionGap }

export interface LiveTranscriptionTextStreamReader {
  info: {
    id: string
    attributes?: Record<string, string>
  }
  readAll: () => Promise<string>
}

export interface LiveTranscriptionState {
  roomId: string
  status: LiveTranscriptionStatus
  segments: LiveTranscriptionSegment[]
  gaps: LiveTranscriptionGap[]
  truncated: boolean
  nextSequence: number
}

export type LiveTranscriptionAction =
  | { type: 'reset'; roomId: string }
  | { type: 'ingest'; event: LiveTranscriptionTransportEvent }
  | { type: 'status'; status: LiveTranscriptionStatus }
  | { type: 'gap'; gap: LiveTranscriptionGap }

export type LiveTranscriptionEventHandler = (
  segments: TranscriptionSegment[],
  participant?: Participant,
  publication?: TrackPublication
) => void
