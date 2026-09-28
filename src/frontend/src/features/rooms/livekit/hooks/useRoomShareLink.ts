import { useQuery } from '@tanstack/react-query'
import { keys } from '@/api/queryKeys'
import { getRouteUrl } from '@/navigation/getRouteUrl'
import { createGuestInvitationShare } from '../../api/createGuestInvitationShare'

const CANONICAL_ROOM_SLUG = /^room_[a-f0-9]{32}$/

export const useRoomShareLink = (roomSlug: string | undefined) => {
  const isCanonicalRoom = CANONICAL_ROOM_SLUG.test(roomSlug ?? '')
  const query = useQuery({
    queryKey: [keys.room, roomSlug, 'guest-invitation-share'],
    queryFn: () => createGuestInvitationShare(roomSlug ?? ''),
    enabled: isCanonicalRoom,
    retry: false,
    staleTime: 3 * 60 * 1000,
  })
  const ordinaryRoomUrl = roomSlug ? getRouteUrl('room', roomSlug) : ''

  return {
    shareUrl: isCanonicalRoom ? (query.data ?? '') : ordinaryRoomUrl,
    isShareLinkPending: isCanonicalRoom && query.isPending,
    shareLinkError: isCanonicalRoom ? query.error : null,
  }
}
