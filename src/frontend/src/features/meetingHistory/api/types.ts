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

export type MeetingContentProjection = {
  version: 1
  state: MeetingContentStatus
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
  endedAt: Date | null
  participantCount: number | null
  summaryStatus: MeetingContentStatus
  transcriptStatus: MeetingContentStatus
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

export type MeetingSummary = {
  status: MeetingContentStatus
  projection: MeetingContentProjection
  paragraphs: string[]
  sections: MeetingSummarySection[]
}

export type MeetingTranscriptSegment = {
  id: string
  startMs: number
  speaker: string | null
  text: string
}

export type MeetingTranscript = {
  status: MeetingContentStatus
  projection: MeetingContentProjection
  segments: MeetingTranscriptSegment[]
  truncated: boolean
}

export type MeetingHistoryDetail = MeetingHistoryItem & {
  summary: MeetingSummary
  transcript: MeetingTranscript
}
