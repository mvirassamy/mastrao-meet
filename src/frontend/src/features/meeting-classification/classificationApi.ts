import { fetchApi } from '@/api/fetchApi'
import type { MatterDestination } from './ClassificationDialog'

export type MeetingClassification = {
  version: 1
  meeting_ref: string
  matter_ref: string | null
  classification_version: number
  can_classify: boolean
}
export type ClassifyCommand = {
  version: 1
  meeting_ref: string
  matter_ref: string
  expected_classification_version: number
  command_id: string
}
export type CreateMatterCommand = {
  version: 1
  command_id: string
  display_label: string
}
export type MatterPage = {
  items: MatterDestination[]
  next_cursor: string | null
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Invalid classification response')
  return value as Record<string, unknown>
}
function reference(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9_-]{16,160}$/.test(value)
}
function parseClassification(
  value: unknown,
  meetingId: string
): MeetingClassification {
  const data = record(value)
  if (
    data.version !== 1 ||
    data.meeting_ref !== meetingId ||
    (data.matter_ref !== null && !reference(data.matter_ref)) ||
    !Number.isSafeInteger(data.classification_version) ||
    Number(data.classification_version) < 0 ||
    typeof data.can_classify !== 'boolean'
  )
    throw new Error('Invalid classification response')
  return data as MeetingClassification
}
function parseMatter(value: unknown): MatterDestination {
  const data = record(value)
  if (
    data.version !== 1 ||
    !reference(data.matter_ref) ||
    typeof data.display_label !== 'string' ||
    !data.display_label.trim()
  )
    throw new Error('Invalid matter response')
  return { matter_ref: data.matter_ref, display_label: data.display_label }
}
const classificationPath = (meetingId: string) =>
  `meetings/history/${encodeURIComponent(meetingId)}/classification/`

export async function readClassification(
  meetingId: string,
  signal: AbortSignal
) {
  return parseClassification(
    await fetchApi<unknown>(classificationPath(meetingId), {
      signal,
      cache: 'no-store',
    }),
    meetingId
  )
}
export async function classifyMeeting(
  command: ClassifyCommand,
  signal: AbortSignal
) {
  const raw = await fetchApi<unknown>(classificationPath(command.meeting_ref), {
    method: 'POST',
    body: JSON.stringify(command),
    signal,
  })
  const result = parseClassification(raw, command.meeting_ref)
  if (
    result.matter_ref !== command.matter_ref ||
    typeof record(raw).idempotent_replay !== 'boolean'
  )
    throw new Error('Invalid classification receipt')
  return result
}
export async function findMatters(
  query: string,
  cursor: string | null,
  signal: AbortSignal
): Promise<MatterPage> {
  const params = new URLSearchParams({
    query,
    limit: '20',
    writable_only: 'true',
  })
  if (cursor) params.set('cursor', cursor)
  const data = record(
    await fetchApi<unknown>(`matters/?${params}`, { signal, cache: 'no-store' })
  )
  if (
    data.version !== 1 ||
    !Array.isArray(data.items) ||
    (data.next_cursor !== null && typeof data.next_cursor !== 'string')
  )
    throw new Error('Invalid matter list')
  return { items: data.items.map(parseMatter), next_cursor: data.next_cursor }
}
export async function createMatter(
  command: CreateMatterCommand,
  signal: AbortSignal
) {
  return parseMatter(
    await fetchApi<unknown>('matters/', {
      method: 'POST',
      body: JSON.stringify(command),
      signal,
    })
  )
}
