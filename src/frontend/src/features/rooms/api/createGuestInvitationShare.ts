import { fetchApi } from '@/api/fetchApi'

const SHARE_REFERENCE = /^share_[A-Za-z0-9_-]{32}$/
const ORGANIZATION = /^[A-Za-z0-9._:-]{1,200}$/

export class GuestInvitationShareContractError extends Error {
  constructor() {
    super('Unexpected guest invitation response')
  }
}

const parseInviteUrl = (value: unknown) => {
  if (typeof value !== 'string') throw new GuestInvitationShareContractError()
  const url = new URL(value)
  const fragment = new URLSearchParams(url.hash.slice(1))
  const share = fragment.get('share')
  const organization = fragment.get('organization')
  if (
    url.origin !== window.location.origin ||
    url.username ||
    url.password ||
    url.pathname !== '/guest' ||
    url.search ||
    !share ||
    !SHARE_REFERENCE.test(share) ||
    !organization ||
    !ORGANIZATION.test(organization) ||
    [...fragment.keys()].length !== 2
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
