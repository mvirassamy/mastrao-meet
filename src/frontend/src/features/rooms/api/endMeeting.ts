import { fetchApi } from '@/api/fetchApi'

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
  closeRequestId: string
): Promise<EndMeetingResponse> =>
  fetchApi(`/rooms/${roomId}/end/`, {
    method: 'POST',
    body: JSON.stringify({ close_request_id: closeRequestId }),
  })
