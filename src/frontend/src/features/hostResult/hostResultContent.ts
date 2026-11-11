import type {
  MeetingSummary,
  MeetingTranscript,
} from '@/features/meetingHistory/api/types'
import type {
  HostResult,
  HostResultSegment,
  HostResultSummaryItem,
} from './api/readHostResult'

/*
 * The host report comes from Core in its own shape. These functions turn it
 * into the summary and transcript the meeting history already displays.
 */

type ItemKind = HostResultSummaryItem['kind']

const SECTION_ORDER: ItemKind[] = ['decision', 'action', 'deadline']

export const hostSummary = (
  summary: HostResult['summary'],
  sectionTitle: (kind: ItemKind) => string
): Pick<
  MeetingSummary,
  'status' | 'productState' | 'paragraphs' | 'sections'
> => ({
  status: 'available',
  productState:
    summary.editorial_state === 'published'
      ? 'published_reviewed'
      : 'generated_pending_review',
  paragraphs: summary.summary.text
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean),
  sections: SECTION_ORDER.map((kind) => ({
    title: sectionTitle(kind),
    items: summary.items
      .filter((item) => item.kind === kind)
      .map((item) => item.statement),
  })).filter((section) => section.items.length > 0),
})

const speakerLabel = (
  speaker: HostResultSegment['speaker'],
  anonymousSpeaker: (index: number) => string
) => {
  if (speaker === null) return null
  if (speaker.kind === 'participant') return speaker.label
  return anonymousSpeaker(speaker.index)
}

export const hostTranscript = (
  transcript: HostResult['transcript'],
  anonymousSpeaker: (index: number) => string
): Pick<MeetingTranscript, 'status' | 'segments' | 'truncated'> => ({
  status: 'available',
  truncated: false,
  segments: transcript.segments.map((segment) => ({
    id: segment.segment_id,
    startMs: segment.start_ms,
    speaker: speakerLabel(segment.speaker, anonymousSpeaker),
    text: segment.text,
  })),
})
