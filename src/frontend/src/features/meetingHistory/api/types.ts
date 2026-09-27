/**
 * Frontend view of the meeting history contract proposed to the backend.
 * Paths and field names stay provisional until the backend confirms them.
 */
export const meetingContentStatuses = [
  'processing',
  'available',
  'absent',
  'failed',
] as const

export type MeetingContentStatus = (typeof meetingContentStatuses)[number]

export type MeetingHistoryItem = {
  id: string
  title: string | null
  startedAt: Date
  endedAt: Date | null
  participantCount: number | null
  summaryStatus: MeetingContentStatus
  transcriptStatus: MeetingContentStatus
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
  segments: MeetingTranscriptSegment[]
  truncated: boolean
}

export type MeetingHistoryDetail = MeetingHistoryItem & {
  summary: MeetingSummary
  transcript: MeetingTranscript
}
