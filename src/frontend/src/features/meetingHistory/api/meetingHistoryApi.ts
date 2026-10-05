import { fetchApi } from '@/api/fetchApi'
import {
  meetingContentStatuses,
  type MeetingContentProjection,
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

const SHA256_DIGEST = /^[a-f0-9]{64}$/u

export const normalizeContentStatus = (value: unknown): MeetingContentStatus =>
  meetingContentStatuses.includes(value as MeetingContentStatus)
    ? (value as MeetingContentStatus)
    : value === 'processing'
      ? 'transcribing'
      : 'unknown'

const legacyProjectionState = (
  value: unknown,
  kind: 'summary' | 'transcript'
): MeetingContentStatus => {
  if (value === 'available' || value === 'failed') return value
  if (value === 'processing') return 'transcribing'
  if (value === 'absent')
    return kind === 'summary' ? 'not_started' : 'audio_unavailable'
  return normalizeContentStatus(value)
}

const asRevision = (value: unknown) =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : 0

const asDigest = (value: unknown) =>
  typeof value === 'string' && SHA256_DIGEST.test(value) ? value : null

const normalizeProjection = (
  value: unknown,
  fallbackStatus: unknown,
  kind: 'summary' | 'transcript'
): MeetingContentProjection => {
  const record = isRecord(value) ? value : null
  const source = record && isRecord(record.source) ? record.source : null
  const sourceDigest = asDigest(source?.digest)
  return {
    version: 1,
    state: record
      ? normalizeContentStatus(record.state)
      : legacyProjectionState(fallbackStatus, kind),
    revision: asRevision(record?.revision),
    digest: asDigest(record?.digest),
    source: sourceDigest
      ? { revision: asRevision(source?.revision), digest: sourceDigest }
      : null,
    refreshPending: false,
  }
}

const isReadable = (projection: MeetingContentProjection) =>
  projection.state === 'available' || projection.state === 'partial'

const sameSource = (
  left: MeetingContentProjection['source'],
  right: MeetingContentProjection['source']
) =>
  left === right ||
  (left !== null &&
    right !== null &&
    left.revision === right.revision &&
    left.digest === right.digest)

const sameProjectionIdentity = (
  left: MeetingContentProjection,
  right: MeetingContentProjection
) =>
  left.state === right.state &&
  left.digest === right.digest &&
  sameSource(left.source, right.source)

const summaryMatchesTranscript = (
  summary: MeetingContentProjection,
  transcript: MeetingContentProjection
) =>
  summary.source !== null &&
  transcript.digest !== null &&
  summary.source.revision === transcript.revision &&
  summary.source.digest === transcript.digest

export const mergeContentProjection = (
  current: MeetingContentProjection,
  incoming: MeetingContentProjection
): MeetingContentProjection => {
  const observedRevision = Math.max(
    current.revision,
    current.pendingRevision ?? current.revision
  )
  if (incoming.revision > observedRevision) {
    if (isReadable(current) && incoming.state === 'unknown') {
      return {
        ...current,
        refreshPending: true,
        pendingRevision: incoming.revision,
      }
    }
    return incoming
  }
  if (incoming.revision < observedRevision) return current
  if (incoming.revision > current.revision) {
    return isReadable(current) && incoming.state === 'unknown'
      ? {
          ...current,
          refreshPending: true,
          pendingRevision: incoming.revision,
        }
      : incoming
  }
  if (isReadable(current) && !isReadable(incoming)) {
    return incoming.state === 'unknown'
      ? { ...current, refreshPending: true }
      : current
  }
  if (
    isReadable(current) &&
    isReadable(incoming) &&
    !sameProjectionIdentity(current, incoming)
  ) {
    return { ...current, refreshPending: true }
  }
  return incoming
}

export const mergeHistoryItem = (
  current: MeetingHistoryItem,
  incoming: MeetingHistoryItem
): MeetingHistoryItem => {
  const summaryProjection = mergeContentProjection(
    current.summaryProjection,
    incoming.summaryProjection
  )
  const transcriptProjection = mergeContentProjection(
    current.transcriptProjection,
    incoming.transcriptProjection
  )
  return {
    ...incoming,
    summaryStatus: summaryProjection.state,
    transcriptStatus: transcriptProjection.state,
    summaryProjection,
    transcriptProjection,
  }
}

export const mergeHistoryDetail = (
  current: MeetingHistoryDetail,
  incoming: MeetingHistoryDetail
): MeetingHistoryDetail => {
  const item = mergeHistoryItem(current, incoming)
  const coherentSummaryProjection =
    item.summaryProjection.sourceRequired === true &&
    isReadable(item.summaryProjection) &&
    !summaryMatchesTranscript(item.summaryProjection, item.transcriptProjection)
      ? {
          ...item.summaryProjection,
          state: 'unknown' as const,
          refreshPending: true,
        }
      : item.summaryProjection
  const dropSummary = coherentSummaryProjection !== item.summaryProjection
  const keepSummary =
    coherentSummaryProjection === current.summaryProjection ||
    (coherentSummaryProjection.refreshPending &&
      summaryMatchesTranscript(
        current.summaryProjection,
        item.transcriptProjection
      ) &&
      isReadable(current.summaryProjection))
  const keepTranscript =
    item.transcriptProjection === current.transcriptProjection ||
    (item.transcriptProjection.refreshPending &&
      isReadable(current.transcriptProjection))
  return {
    ...incoming,
    ...item,
    summaryStatus: coherentSummaryProjection.state,
    summaryProjection: coherentSummaryProjection,
    summary: keepSummary
      ? { ...current.summary, projection: coherentSummaryProjection }
      : dropSummary
        ? {
            ...incoming.summary,
            status: coherentSummaryProjection.state,
            projection: coherentSummaryProjection,
            paragraphs: [],
            sections: [],
          }
        : { ...incoming.summary, projection: coherentSummaryProjection },
    transcript: keepTranscript
      ? { ...current.transcript, projection: item.transcriptProjection }
      : { ...incoming.transcript, projection: item.transcriptProjection },
  }
}

const normalizeItem = (raw: unknown): MeetingHistoryItem | null => {
  if (!isRecord(raw)) return null
  const id = asString(raw.id)
  const startedAt = asDate(raw.started_at)
  if (!id || !startedAt) return null
  const participantCount =
    typeof raw.participant_count === 'number' && raw.participant_count >= 0
      ? raw.participant_count
      : null
  const participantNames = Array.isArray(raw.participant_names)
    ? raw.participant_names.map(asString).filter((name) => name !== null)
    : []

  const summaryProjection = normalizeProjection(
    raw.summary_projection,
    raw.summary_status,
    'summary'
  )
  const transcriptProjection = normalizeProjection(
    raw.transcript_projection,
    raw.transcript_status,
    'transcript'
  )
  return {
    id,
    title: asString(raw.title),
    startedAt,
    scheduledStartAt:
      typeof raw.scheduled_start_at === 'number'
        ? new Date(raw.scheduled_start_at * 1000)
        : null,
    endedAt: asDate(raw.ended_at),
    participantCount,
    participantNames,
    summaryStatus: summaryProjection.state,
    transcriptStatus: transcriptProjection.state,
    summaryProjection,
    transcriptProjection,
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

/** Old responses without projections are refined locally during rollout. */
export const normalizeHistoryDetail = (
  raw: unknown
): MeetingHistoryDetail | null => {
  const item = normalizeItem(raw)
  if (!item || !isRecord(raw)) return null
  const rawSummary = isRecord(raw.summary) ? raw.summary : {}
  const rawTranscript = isRecord(raw.transcript) ? raw.transcript : {}

  let summaryProjection = normalizeProjection(
    raw.summary_projection ?? rawSummary.projection,
    rawSummary.status ?? raw.summary_status,
    'summary'
  )
  summaryProjection = {
    ...summaryProjection,
    sourceRequired: isRecord(raw.summary_projection),
  }
  const paragraphs = (asString(rawSummary.text) ?? '')
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
  const sections = normalizeSections(rawSummary.sections)
  const hasSummaryContent = paragraphs.length > 0 || sections.length > 0

  let transcriptProjection = normalizeProjection(
    raw.transcript_projection ?? rawTranscript.projection,
    rawTranscript.status ?? raw.transcript_status,
    'transcript'
  )
  const segments = normalizeSegments(rawTranscript.segments)

  const hasExplicitSummaryProjection = isRecord(raw.summary_projection)
  if (
    hasExplicitSummaryProjection &&
    isReadable(summaryProjection) &&
    !summaryMatchesTranscript(summaryProjection, transcriptProjection)
  ) {
    summaryProjection = {
      ...summaryProjection,
      state: 'unknown',
      refreshPending: true,
    }
  }

  if (
    !isRecord(raw.summary_projection) &&
    summaryProjection.state === 'available' &&
    !hasSummaryContent
  ) {
    summaryProjection = { ...summaryProjection, state: 'completed_empty' }
  }
  if (
    !isRecord(raw.transcript_projection) &&
    transcriptProjection.state === 'available' &&
    segments.length === 0
  ) {
    transcriptProjection = { ...transcriptProjection, state: 'completed_empty' }
  }

  const summary = {
    status: summaryProjection.state,
    projection: summaryProjection,
    paragraphs: isReadable(summaryProjection) ? paragraphs : [],
    sections: isReadable(summaryProjection) ? sections : [],
  }
  const transcript = {
    status: transcriptProjection.state,
    projection: transcriptProjection,
    segments: isReadable(transcriptProjection) ? segments : [],
    truncated: rawTranscript.truncated === true,
  }

  return {
    ...item,
    summaryStatus: summaryProjection.state,
    transcriptStatus: transcriptProjection.state,
    summaryProjection,
    transcriptProjection,
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
    : rawStatus === 'processing'
      ? 'transcribing'
      : 'unknown'
}
