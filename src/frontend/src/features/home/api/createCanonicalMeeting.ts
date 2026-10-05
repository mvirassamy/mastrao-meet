import { useMutation, useQueryClient } from '@tanstack/react-query'
import { plannedMeetingsQueryKey } from './plannedMeetings'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import type { MeetingSchedule } from '../utils/meetingSchedule'

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
  invitations?: VideoInvitation[]
}

export type VideoInvitation = {
  invitation_ref: string
  email: string
  delivery_state: 'pending' | 'sending' | 'sent' | 'unknown'
}

const readInvitations = (value: unknown): VideoInvitation[] => {
  if (!Array.isArray(value) || value.length > 50)
    throw new CanonicalMeetingContractError()
  return value.map((item: unknown) => {
    if (!item || typeof item !== 'object')
      throw new CanonicalMeetingContractError()
    const invitation = item as Record<string, unknown>
    const { invitation_ref, email, delivery_state } = invitation
    if (
      Object.keys(invitation).length !== 3 ||
      typeof invitation_ref !== 'string' ||
      !/^[A-Za-z0-9_-]{16,160}$/.test(invitation_ref) ||
      typeof email !== 'string' ||
      !['pending', 'sending', 'sent', 'unknown'].includes(
        String(delivery_state)
      )
    )
      throw new CanonicalMeetingContractError()
    return {
      invitation_ref,
      email,
      delivery_state: delivery_state as VideoInvitation['delivery_state'],
    }
  })
}

export type CreateMeetingRequest = {
  idempotencyKey: string
  schedule?: MeetingSchedule
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

export const createCanonicalMeeting = async ({
  idempotencyKey,
  schedule,
}: CreateMeetingRequest): Promise<CanonicalMeeting> => {
  if (!IDEMPOTENCY_KEY.test(idempotencyKey))
    throw new Error('Invalid idempotency key')
  const options: RequestInit = {
    method: 'POST',
    headers: { 'X-Idempotency-Key': idempotencyKey },
  }
  if (schedule) {
    options.body = JSON.stringify({
      ...(schedule.title && { title: schedule.title }),
      ...(schedule.invitees?.length && { invitee_emails: schedule.invitees }),
      scheduled_start_at: schedule.startsAt,
      scheduled_end_at: schedule.endsAt,
      timezone: schedule.timeZone,
    })
  }
  const raw = await fetchApi<Record<string, unknown>>(
    CANONICAL_MEETING_ENDPOINT,
    options
  )
  const { meeting_ref, room_ref } = raw ?? {}
  if (
    typeof meeting_ref !== 'string' ||
    typeof room_ref !== 'string' ||
    !REF.test(meeting_ref) ||
    !REF.test(room_ref)
  )
    throw new CanonicalMeetingContractError()
  if (
    schedule &&
    (raw.scheduled_start_at !== schedule.startsAt ||
      raw.scheduled_end_at !== schedule.endsAt ||
      raw.timezone !== schedule.timeZone ||
      (raw.title ?? '') !== schedule.title)
  )
    throw new CanonicalMeetingContractError()
  const invitations =
    raw.video_invitations === undefined
      ? undefined
      : readInvitations(raw.video_invitations)
  if (
    schedule?.invitees?.length &&
    (!invitations ||
      invitations.length !== schedule.invitees.length ||
      invitations.some(
        (invitation) => !schedule.invitees?.includes(invitation.email)
      ) ||
      new Set(invitations.map((invitation) => invitation.email)).size !==
        invitations.length)
  )
    throw new CanonicalMeetingContractError()
  return {
    meetingRef: meeting_ref,
    roomRef: room_ref,
    ...(invitations && { invitations }),
  }
}

const isRetryable = (error: unknown) =>
  error instanceof ApiError && [502, 503, 504].includes(error.statusCode)

export const canonicalMeetingMutationKey = ['createCanonicalMeeting'] as const

/**
 * The caller generates the key once per action and passes it as the mutation
 * variable: automatic retries replay the exact same key.
 */
export const useCreateCanonicalMeeting = () => {
  const queryClient = useQueryClient()
  return useMutation<CanonicalMeeting, unknown, CreateMeetingRequest>({
    mutationKey: canonicalMeetingMutationKey,
    mutationFn: createCanonicalMeeting,
    onSuccess: (_meeting, request) => {
      if (request.schedule)
        void queryClient.invalidateQueries({
          queryKey: plannedMeetingsQueryKey,
        })
    },
    retry: (failureCount, error) => isRetryable(error) && failureCount < 2,
  })
}
