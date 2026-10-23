import type {
  MeetingContentStatus,
  MeetingHistoryItem,
  MeetingRecordingStatus,
} from '../api/types'

export type MeetingContentKind = 'summary' | 'transcript'

export const SECTION_ICONS: Record<MeetingContentKind, string> = {
  summary: '/assets/illustrations/section-synthese.webp',
  transcript: '/assets/illustrations/section-transcription.webp',
}

export const NOTES_ICON = '/assets/illustrations/section-notes.webp'

export const VIDEO_ICON = '/assets/illustrations/section-video.webp'

export const ASSISTANT_ICON = '/assets/illustrations/assistant.webp'

/** A recording state worth showing: the meeting was actually recorded. */
export type RecordedStatus = Exclude<MeetingRecordingStatus, 'absent'>

/** The video state of a row, or null when the meeting was never recorded. */
export const recordedStatus = (
  item: MeetingHistoryItem
): RecordedStatus | null =>
  item.recordingProjected === true && item.recordingStatus !== 'absent'
    ? item.recordingStatus
    : null

/** Where a summary or transcript stands, as the interface groups it. */
export type ContentPhase = 'ready' | 'partial' | 'pending' | 'failed' | 'absent'

export const contentPhase = (status: MeetingContentStatus): ContentPhase => {
  switch (status) {
    case 'available':
      return 'ready'
    case 'partial':
      return 'partial'
    case 'unknown':
    case 'waiting_for_audio':
    case 'transcribing':
      return 'pending'
    case 'failed':
      return 'failed'
    case 'not_started':
    case 'completed_empty':
    case 'audio_unavailable':
      return 'absent'
  }
}

/** Status colours shared by the list icons and the section states. */
export const STATUS_COLORS = {
  ready: '#2f9e5a',
  running: 'var(--colors-primary)',
  failed: '#c4323d',
  absent: '#98a2b3',
} as const
