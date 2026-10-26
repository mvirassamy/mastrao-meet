import type { MeetingNativeCaptureDomain } from './meetingNativeCaptureDomain'
import type { LiveFullSnapshot } from './meetingLiveFull'
import type { MeetingLiveObservedDomain } from './meetingLiveObservedDomain'
import type { NativeFullSnapshot } from './meetingNativeFull'

/**
 * Frontend view of the meeting history contract proposed to the backend.
 * Paths and field names stay provisional until the backend confirms them.
 */
export const meetingContentStatuses = [
  'unknown',
  'not_started',
  'waiting_for_audio',
  'transcribing',
  'available',
  'completed_empty',
  'audio_unavailable',
  'partial',
  'failed',
] as const

export type MeetingContentStatus = (typeof meetingContentStatuses)[number]

export type MeetingRecordingStatus =
  | 'absent'
  | 'processing'
  | 'available'
  | 'expired'
  | 'failed'
  | 'unknown'

export type MeetingRecordingAccess = {
  actionPath: string
  matterRef: string
  meetingRef: string
  recordingRef: string
  artifactRef: string
}

export type MeetingRecording = {
  status: MeetingRecordingStatus
  retentionExpiresAt: Date | null
  access: MeetingRecordingAccess | null
}

export type MeetingContentProjection = {
  version: 1
  state: MeetingContentStatus
  processing_state?: 'temporarily_suspended'
  revision: number
  digest: string | null
  source: { revision: number; digest: string } | null
  /** Client-only: a newer projection was temporarily unreadable. */
  refreshPending?: boolean
  /** Client-only: highest canonical revision observed while retaining old content. */
  pendingRevision?: number
  /** Client-only: this server projection must be bound to a transcript source. */
  sourceRequired?: boolean
}

export type MeetingHistoryItem = {
  id: string
  title: string | null
  startedAt: Date
  /** Additive list metadata; planned home rows come from the canonical day read. */
  scheduledStartAt?: Date | null
  endedAt: Date | null
  participantCount: number | null
  /** Display names of the first participants, in the server's order. */
  participantNames: string[]
  summaryStatus: MeetingContentStatus
  transcriptStatus: MeetingContentStatus
  recordingStatus: MeetingRecordingStatus
  /** False while an older Platform response does not project video metadata. */
  recordingProjected?: boolean
  summaryProjection: MeetingContentProjection
  transcriptProjection: MeetingContentProjection
}

export type MeetingHistoryPage = {
  items: MeetingHistoryItem[]
  nextCursor: string | null
}

export type MeetingSummarySection = {
  title: string
  items: string[]
}

export type MeetingSummaryProductState =
  | 'generated_pending_review'
  | 'validated_ready_to_publish'
  | 'published_reviewed'
  | 'stale_draft'

export type MeetingSummary = {
  status: MeetingContentStatus
  productState?: MeetingSummaryProductState
  projection: MeetingContentProjection
  paragraphs: string[]
  sections: MeetingSummarySection[]
}

export type MeetingTranscriptSegment = {
  id: string
  startMs: number
  speaker: string | null
  text: string
  sourceEpoch?: {
    epochRef: string
    timeBasis: 'source_relative_ms'
  }
}

export type MeetingTranscript = {
  status: MeetingContentStatus
  projection: MeetingContentProjection
  segments: MeetingTranscriptSegment[]
  truncated: boolean
  /** Retained live sources never establish canonical transcript readiness. */
  liveSources?: MeetingLiveSource[]
  liveTruncated?: boolean
  liveFullSnapshot?: LiveFullSnapshot
  liveObservedDomain?: MeetingLiveObservedDomain
  nativeCoverage?: MeetingNativeCoverage
  nativePartial?: MeetingNativePartial
  nativeFullSnapshot?: NativeFullSnapshot
}

/** Auxiliary native consultation; never a canonical transcript revision. */
export type MeetingNativePartial = {
  version: 1
  meetingRef: string
  snapshotDigest: string
  coverage: 'partial_sources'
  segments: (MeetingTranscriptSegment & {
    sourceEpoch: NonNullable<MeetingTranscriptSegment['sourceEpoch']>
  })[]
  truncated: boolean
}

export type MeetingNativeCoverage = {
  epochsUnavailableReason?:
    | 'epoch_limit_exceeded'
    | 'result_bytes_limit_exceeded'
  captureDomain?: MeetingNativeCaptureDomain
  meetingCoverage: 'unverified'
  observationsAvailable: boolean
  truncated: boolean
  tracks: {
    observationRef: string
    captureState: 'admitted' | 'unadmitted'
    epochRefs?: string[]
  }[]
  epochs: {
    epochRef: string
    state: 'pending' | 'failed' | 'available'
  }[]
}

type MeetingLiveEventIdentity = {
  version: 2
  source_ref: string
  leg_ref: string
  sequence: number
}

export type MeetingLiveEvent = MeetingLiveEventIdentity &
  (
    | {
        kind: 'final'
        item_ref: string
        revision: number
        text: string
        language: string | null
        timing:
          | { state: 'unavailable' }
          | {
              state: 'source_buffer'
              sample_rate: 24000
              start_sample: number
              end_sample: number
            }
      }
    | {
        kind: 'gap'
        reason:
          | 'connection_lost'
          | 'provider_failed'
          | 'delivery_failed'
          | 'timing_unavailable'
          | 'capture_incomplete'
      }
    | {
        kind: 'end'
        reason:
          | 'track_unpublished'
          | 'meeting_ended'
          | 'provider_failed'
          | 'capture_incomplete'
        final_event_count: number
      }
  )

export type MeetingLiveSource = {
  sourceRef: string
  participantKind: 'host' | 'guest'
  participantRef: string
  state: 'collecting' | 'ended'
  incomplete: boolean
  confirmedSequence: number
  events: MeetingLiveEvent[]
}

export type MeetingHistoryDetail = MeetingHistoryItem & {
  recording: MeetingRecording
  summary: MeetingSummary
  transcript: MeetingTranscript
}
