import { fetchApi } from '@/api/fetchApi'
import { ApiError } from '@/api/ApiError'

export type EndMeetingResponse = {
  version: 1
  meeting_ref: string
  room_ref: string
  state: 'ending' | 'ended'
  state_version: number
  requested_at: number
  ended_at?: number
}

export const endMeeting = (
  roomId: string,
  closeRequestId: string,
  signal?: AbortSignal
): Promise<EndMeetingResponse> =>
  fetchApi(`/rooms/${roomId}/end/`, {
    method: 'POST',
    body: JSON.stringify({ close_request_id: closeRequestId }),
    signal,
  })

export const isRetryableEndMeetingError = (error: unknown) =>
  !(error instanceof ApiError) ||
  error.statusCode === 408 ||
  error.statusCode === 429 ||
  error.statusCode >= 500
