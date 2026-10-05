import { useQuery } from '@tanstack/react-query'
import { keys } from '@/api/queryKeys'
import { getRouteUrl } from '@/navigation/getRouteUrl'
import { createGuestInvitationShare } from '../../api/createGuestInvitationShare'

const CANONICAL_ROOM_SLUG = /^room_[a-f0-9]{32}$/
// A durable locator keeps its original identity across host views.
// Core checks current access when the guest actually redeems it.

export const useRoomShareLink = (roomSlug: string | undefined) => {
  const isCanonicalRoom = CANONICAL_ROOM_SLUG.test(roomSlug ?? '')
  const query = useQuery({
    queryKey: [keys.room, roomSlug, 'guest-invitation-share'],
    queryFn: () => createGuestInvitationShare(roomSlug ?? ''),
    enabled: isCanonicalRoom,
    retry: false,
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
  })
  const ordinaryRoomUrl = roomSlug ? getRouteUrl('room', roomSlug) : ''

  return {
    shareUrl: isCanonicalRoom ? (query.data ?? '') : ordinaryRoomUrl,
    isShareLinkPending: isCanonicalRoom && query.isPending,
    shareLinkError: isCanonicalRoom ? query.error : null,
  }
}
