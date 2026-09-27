import { useMutation } from '@tanstack/react-query'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'

/**
 * Canonical Meet creation backed by the Platform facade (Cabinet Core).
 * The backend redeems the host handoff server-side and attaches the host
 * grant to the current session: the browser only receives references.
 */
export const CANONICAL_MEETING_ENDPOINT = 'meetings/'

export type CanonicalMeeting = {
  meetingRef: string
  /** Also the Meet room slug: the room URL is /{roomRef}. */
  roomRef: string
}

const IDEMPOTENCY_KEY = /^[A-Za-z0-9_-]{16,128}$/
const REF = /^[A-Za-z0-9_-]{1,128}$/

/**
 * One key per user action ("instant" or "for later"), reused by every retry
 * of that action so the backend never creates the meeting twice.
 */
export const createIdempotencyKey = () => {
  const key =
    typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : Array.from(crypto.getRandomValues(new Uint8Array(24)), (byte) =>
          byte.toString(16).padStart(2, '0')
        ).join('')
  return `meet_${key}`
}

export class CanonicalMeetingContractError extends Error {
  constructor() {
    super('Unexpected canonical meeting response')
  }
}

export const createCanonicalMeeting = async (
  idempotencyKey: string
): Promise<CanonicalMeeting> => {
  if (!IDEMPOTENCY_KEY.test(idempotencyKey))
    throw new Error('Invalid idempotency key')
  const raw = await fetchApi<Record<string, unknown>>(
    CANONICAL_MEETING_ENDPOINT,
    {
      method: 'POST',
      headers: { 'X-Idempotency-Key': idempotencyKey },
    }
  )
  const { meeting_ref, room_ref } = raw ?? {}
  if (
    typeof meeting_ref !== 'string' ||
    typeof room_ref !== 'string' ||
    !REF.test(meeting_ref) ||
    !REF.test(room_ref)
  )
    throw new CanonicalMeetingContractError()
  return { meetingRef: meeting_ref, roomRef: room_ref }
}

const isRetryable = (error: unknown) =>
  error instanceof ApiError && [502, 503, 504].includes(error.statusCode)

export const canonicalMeetingMutationKey = ['createCanonicalMeeting'] as const

/**
 * The caller generates the key once per action and passes it as the mutation
 * variable: automatic retries replay the exact same key.
 */
export const useCreateCanonicalMeeting = () =>
  useMutation<CanonicalMeeting, unknown, string>({
    mutationKey: canonicalMeetingMutationKey,
    mutationFn: createCanonicalMeeting,
    retry: (failureCount, error) => isRetryable(error) && failureCount < 2,
  })
