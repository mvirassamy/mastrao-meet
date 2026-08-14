import { ApiError } from '@/api/ApiError'

export type GuestRedemptionResponse = { room_url: string }

const handoffUrl = () => {
  const origin =
    import.meta.env.VITE_API_BASE_URL ||
    (typeof window !== 'undefined' ? window.location.origin : '')
  return `${origin.replace(/\/$/, '')}/handoff/guest/`
}

const establishGuestSession = async () => {
  const response = await fetch(`${handoffUrl()}session/`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  })
  if (!response.ok) throw new ApiError(response.status, await response.json())
}

export const redeemGuestInvitation = async (
  guestInvitation: string,
  redemptionId: string
) => {
  await establishGuestSession()
  const response = await fetch(handoffUrl(), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      guest_invitation: guestInvitation,
      redemption_id: redemptionId,
    }),
  })
  const result = (await response.json()) as GuestRedemptionResponse
  if (!response.ok) throw new ApiError(response.status, result)
  return result
}
