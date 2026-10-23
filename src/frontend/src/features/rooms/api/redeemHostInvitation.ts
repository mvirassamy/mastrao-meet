import { ApiError } from '@/api/ApiError'

export type HostInvitationExchangeResponse = { room_url: string }

const exchangeUrl = () => {
  const origin =
    import.meta.env.VITE_API_BASE_URL ||
    (typeof window !== 'undefined' ? window.location.origin : '')
  return `${origin.replace(/\/$/, '')}/handoff/host/invitation/`
}

const establishSessionUrl = () => `${exchangeUrl()}session/`

const establishExchangeSession = async () => {
  const response = await fetch(establishSessionUrl(), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  })
  if (!response.ok) {
    const result = await response.json()
    throw new ApiError(response.status, result)
  }
}

export const redeemHostInvitation = async (
  hostInvitation: string,
  exchangeId: string
) => {
  await establishExchangeSession()
  const response = await fetch(exchangeUrl(), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      host_invitation: hostInvitation,
      exchange_id: exchangeId,
    }),
  })
  const result = (await response.json()) as HostInvitationExchangeResponse
  if (!response.ok) throw new ApiError(response.status, result)
  return result
}
