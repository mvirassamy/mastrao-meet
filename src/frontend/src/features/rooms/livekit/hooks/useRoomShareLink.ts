import { useQuery } from '@tanstack/react-query'
import { keys } from '@/api/queryKeys'
import { getRouteUrl } from '@/navigation/getRouteUrl'
import { createGuestInvitationShare } from '../../api/createGuestInvitationShare'

const CANONICAL_ROOM_SLUG = /^room_[a-f0-9]{32}$/
// Core guest invitations live four hours; keep one link well inside that
// window. Every fetch mints a new invitation, so never refetch implicitly.
const SHARE_LINK_LIFETIME_MS = 3 * 60 * 60 * 1000

export const useRoomShareLink = (roomSlug: string | undefined) => {
  const isCanonicalRoom = CANONICAL_ROOM_SLUG.test(roomSlug ?? '')
  const query = useQuery({
    queryKey: [keys.room, roomSlug, 'guest-invitation-share'],
    queryFn: () => createGuestInvitationShare(roomSlug ?? ''),
    enabled: isCanonicalRoom,
    retry: false,
    staleTime: SHARE_LINK_LIFETIME_MS,
    gcTime: SHARE_LINK_LIFETIME_MS,
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
