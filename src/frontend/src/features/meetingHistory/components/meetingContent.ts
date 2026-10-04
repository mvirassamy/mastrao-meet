import type { MeetingContentStatus } from '../api/types'

export type MeetingContentKind = 'summary' | 'transcript'

export const SECTION_ICONS: Record<MeetingContentKind, string> = {
  summary: '/assets/illustrations/section-synthese.webp',
  transcript: '/assets/illustrations/section-transcription.webp',
}

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
