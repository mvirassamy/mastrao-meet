export type GuestLink =
  | { kind: 'durable'; organization: string; share: string }
  | { kind: 'legacy'; invitation: string }

let capturedInvitation: GuestLink | null | undefined

export const consumeGuestInvitationFragment = () => {
  if (capturedInvitation !== undefined) return capturedInvitation
  const fragment = new URLSearchParams(window.location.hash.slice(1))
  const organization = fragment.get('organization')
  const share = fragment.get('share')
  const invite = fragment.get('invite')
  capturedInvitation = null
  if (
    [...fragment.keys()].length === 2 &&
    organization &&
    /^[A-Za-z0-9._:-]{1,200}$/.test(organization) &&
    share &&
    /^share_[A-Za-z0-9_-]{32}$/.test(share)
  ) {
    capturedInvitation = { kind: 'durable', organization, share }
  } else if (
    invite &&
    invite.length <= 16384 &&
    [...fragment.keys()].length === 1
  ) {
    // Existing issued legacy invitations remain redeemable under their original limits.
    capturedInvitation = { kind: 'legacy', invitation: invite }
  }
  window.history.replaceState(
    window.history.state,
    '',
    `${window.location.pathname}${window.location.search}`
  )
  return capturedInvitation
}

export const guestRedemptionId = (link: GuestLink) => {
  if (link.kind === 'legacy')
    return `redemption_${crypto.randomUUID().replaceAll('-', '')}`
  const key = `mastrao-share-attempt:${link.organization}:${link.share}`
  const previous = sessionStorage.getItem(key)
  if (previous && /^redemption_[a-f0-9]{32}$/.test(previous)) return previous
  const id = `redemption_${crypto.randomUUID().replaceAll('-', '')}`
  sessionStorage.setItem(key, id)
  return id
}

export const forgetGuestRedemption = (link: GuestLink) => {
  if (link.kind === 'durable') {
    sessionStorage.removeItem(
      `mastrao-share-attempt:${link.organization}:${link.share}`
    )
  }
}
