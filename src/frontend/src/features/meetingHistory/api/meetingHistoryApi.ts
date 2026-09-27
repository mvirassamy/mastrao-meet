import { fetchApi } from '@/api/fetchApi'
import {
  meetingContentStatuses,
  type MeetingContentStatus,
  type MeetingHistoryDetail,
  type MeetingHistoryItem,
  type MeetingHistoryPage,
  type MeetingSummarySection,
  type MeetingTranscriptSegment,
} from './types'

/** Provisional Meet endpoint; the backend owns the final path and rights. */
export const MEETING_HISTORY_ENDPOINT = 'meetings/history/'

type RawRecord = Record<string, unknown>

const isRecord = (value: unknown): value is RawRecord =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const asString = (value: unknown) =>
  typeof value === 'string' && value.trim() !== '' ? value.trim() : null

const asDate = (value: unknown) => {
  if (typeof value !== 'string') return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

export const normalizeContentStatus = (value: unknown): MeetingContentStatus =>
  meetingContentStatuses.includes(value as MeetingContentStatus)
    ? (value as MeetingContentStatus)
    : 'absent'

const normalizeItem = (raw: unknown): MeetingHistoryItem | null => {
  if (!isRecord(raw)) return null
  const id = asString(raw.id)
  const startedAt = asDate(raw.started_at)
  if (!id || !startedAt) return null
  const participantCount =
    typeof raw.participant_count === 'number' && raw.participant_count >= 0
      ? raw.participant_count
      : null

  return {
    id,
    title: asString(raw.title),
    startedAt,
    endedAt: asDate(raw.ended_at),
    participantCount,
    summaryStatus: normalizeContentStatus(raw.summary_status),
    transcriptStatus: normalizeContentStatus(raw.transcript_status),
  }
}

export const sortMostRecentFirst = <T extends { startedAt: Date }>(
  items: T[]
) =>
  [...items].sort(
    (left, right) => right.startedAt.getTime() - left.startedAt.getTime()
  )

export const normalizeHistoryPage = (raw: unknown): MeetingHistoryPage => {
  const record = isRecord(raw) ? raw : {}
  const results = Array.isArray(record.results) ? record.results : []
  return {
    items: sortMostRecentFirst(
      results
        .map(normalizeItem)
        .filter((item): item is MeetingHistoryItem => item !== null)
    ),
    nextCursor: asString(record.next_cursor),
  }
}

const normalizeSections = (value: unknown): MeetingSummarySection[] =>
  (Array.isArray(value) ? value : []).flatMap((section) => {
    if (!isRecord(section)) return []
    const title = asString(section.title)
    const items = (Array.isArray(section.items) ? section.items : [])
      .map(asString)
      .filter((item): item is string => item !== null)
    return title && items.length > 0 ? [{ title, items }] : []
  })

const normalizeSegments = (value: unknown): MeetingTranscriptSegment[] =>
  (Array.isArray(value) ? value : [])
    .flatMap((segment, index) => {
      if (!isRecord(segment)) return []
      const text = asString(segment.text)
      if (!text) return []
      const startMs =
        typeof segment.start_ms === 'number' && segment.start_ms >= 0
          ? segment.start_ms
          : 0
      return [
        {
          id: asString(segment.id) ?? `segment-${index}`,
          startMs,
          speaker: asString(segment.speaker),
          text,
        },
      ]
    })
    .sort((left, right) => left.startMs - right.startMs)

/**
 * "available" without readable content is shown as absent so the interface
 * never presents an empty block as a finished summary or transcript.
 */
export const normalizeHistoryDetail = (
  raw: unknown
): MeetingHistoryDetail | null => {
  const item = normalizeItem(raw)
  if (!item || !isRecord(raw)) return null
  const rawSummary = isRecord(raw.summary) ? raw.summary : {}
  const rawTranscript = isRecord(raw.transcript) ? raw.transcript : {}

  const summaryStatus = normalizeContentStatus(
    rawSummary.status ?? item.summaryStatus
  )
  const paragraphs = (asString(rawSummary.text) ?? '')
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
  const sections = normalizeSections(rawSummary.sections)
  const hasSummaryContent = paragraphs.length > 0 || sections.length > 0

  const transcriptStatus = normalizeContentStatus(
    rawTranscript.status ?? item.transcriptStatus
  )
  const segments = normalizeSegments(rawTranscript.segments)

  const summary = {
    status:
      summaryStatus === 'available' && !hasSummaryContent
        ? ('absent' as const)
        : summaryStatus,
    paragraphs: summaryStatus === 'available' ? paragraphs : [],
    sections: summaryStatus === 'available' ? sections : [],
  }
  const transcript = {
    status:
      transcriptStatus === 'available' && segments.length === 0
        ? ('absent' as const)
        : transcriptStatus,
    segments: transcriptStatus === 'available' ? segments : [],
    truncated: rawTranscript.truncated === true,
  }

  return {
    ...item,
    summaryStatus: summary.status,
    transcriptStatus: transcript.status,
    summary,
    transcript,
  }
}

export class MeetingHistoryContractError extends Error {
  constructor() {
    super('Unexpected meeting history response')
  }
}

export const fetchMeetingHistoryPage = async (
  cursor: string | null
): Promise<MeetingHistoryPage> => {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
  const raw = await fetchApi<unknown>(`${MEETING_HISTORY_ENDPOINT}${query}`)
  if (!isRecord(raw) || !Array.isArray(raw.results))
    throw new MeetingHistoryContractError()
  return normalizeHistoryPage(raw)
}

export const fetchMeetingHistoryDetail = async (
  meetingId: string
): Promise<MeetingHistoryDetail> => {
  const raw = await fetchApi<unknown>(
    `${MEETING_HISTORY_ENDPOINT}${encodeURIComponent(meetingId)}/`
  )
  const detail = normalizeHistoryDetail(raw)
  if (!detail) throw new MeetingHistoryContractError()
  return detail
}

/**
 * Asks the backend to prepare the automatic summary. The command is
 * idempotent server-side: 202 → processing, 200 → available, 409 while the
 * transcript is not ready, 404 when the meeting is absent or not allowed.
 */
export const requestMeetingSummary = async (
  meetingId: string
): Promise<MeetingContentStatus> => {
  const raw = await fetchApi<unknown>(
    `${MEETING_HISTORY_ENDPOINT}${encodeURIComponent(meetingId)}/summary/`,
    { method: 'POST' }
  )
  const record = isRecord(raw) ? raw : {}
  const rawStatus =
    record.summary_status ??
    (isRecord(record.summary) ? record.summary.status : undefined)
  return meetingContentStatuses.includes(rawStatus as MeetingContentStatus)
    ? (rawStatus as MeetingContentStatus)
    : 'processing'
}
