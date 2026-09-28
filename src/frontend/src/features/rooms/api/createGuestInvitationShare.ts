import { fetchApi } from '@/api/fetchApi'

const INVITE_CREDENTIAL = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/

export class GuestInvitationShareContractError extends Error {
  constructor() {
    super('Unexpected guest invitation response')
  }
}

const parseInviteUrl = (value: unknown) => {
  if (typeof value !== 'string') throw new GuestInvitationShareContractError()
  const url = new URL(value)
  const invite = new URLSearchParams(url.hash.slice(1)).get('invite')
  if (
    url.origin !== window.location.origin ||
    url.pathname !== '/guest' ||
    url.search ||
    !invite ||
    !INVITE_CREDENTIAL.test(invite)
  )
    throw new GuestInvitationShareContractError()
  return url.href
}

export const createGuestInvitationShare = async (roomSlug: string) => {
  const result = await fetchApi<Record<string, unknown>>(
    `rooms/${encodeURIComponent(roomSlug)}/guest-invitation/`,
    { method: 'POST' }
  )
  return parseInviteUrl(result?.invite_url)
}
