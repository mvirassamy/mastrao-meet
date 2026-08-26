import { isRoomId } from './utils/isRoomValid'

export type PostMeetingOutcome = 'ended' | 'left' | 'removed' | 'duplicate'

const outcomes = new Set<PostMeetingOutcome>([
  'ended',
  'left',
  'removed',
  'duplicate',
])

export const buildPostMeetingPath = (params?: {
  outcome?: PostMeetingOutcome
  roomId?: string
}) => {
  const search = new URLSearchParams()
  if (params?.outcome) search.set('outcome', params.outcome)
  if (params?.roomId && isRoomId(params.roomId)) {
    search.set('room_id', params.roomId)
  }
  const query = search.toString()
  return query ? `/feedback?${query}` : '/feedback'
}

export const readPostMeetingRoute = (search: string) => {
  const params = new URLSearchParams(search)
  const rawOutcome = params.get('outcome')
  const rawRoomId = params.get('room_id')
  return {
    outcome:
      rawOutcome && outcomes.has(rawOutcome as PostMeetingOutcome)
        ? (rawOutcome as PostMeetingOutcome)
        : undefined,
    roomId: rawRoomId && isRoomId(rawRoomId) ? rawRoomId : undefined,
  }
}
