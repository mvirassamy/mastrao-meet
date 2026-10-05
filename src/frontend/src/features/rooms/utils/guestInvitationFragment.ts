export type GuestLink =
  | { kind: 'durable'; organization: string; share: string }
  | { kind: 'legacy'; invitation: string }

let capturedInvitation: GuestLink | null | undefined

/** Reads the guest link locator carried by a URL fragment. */
const parseGuestLinkFragment = (hash: string): GuestLink | null => {
  const fragment = new URLSearchParams(hash.slice(1))
  const organization = fragment.get('organization')
  const share = fragment.get('share')
  const invite = fragment.get('invite')
  if (
    [...fragment.keys()].length === 2 &&
    organization &&
    /^[A-Za-z0-9._:-]{1,200}$/.test(organization) &&
    share &&
    /^share_[A-Za-z0-9_-]{32}$/.test(share)
  ) {
    return { kind: 'durable', organization, share }
  }
  if (invite && invite.length <= 16384 && [...fragment.keys()].length === 1) {
    // Existing issued legacy invitations remain redeemable under their original limits.
    return { kind: 'legacy', invitation: invite }
  }
  return null
}

export const consumeGuestInvitationFragment = () => {
  if (capturedInvitation !== undefined) return capturedInvitation
  capturedInvitation = parseGuestLinkFragment(window.location.hash)
  window.history.replaceState(
    window.history.state,
    '',
    `${window.location.pathname}${window.location.search}`
  )
  return capturedInvitation
}

/** Reads a full guest link pasted by hand; only links to this guest page count. */
export const parsePastedGuestLink = (value: string): GuestLink | null => {
  let url: URL
  try {
    url = new URL(value.trim())
  } catch {
    return null
  }
  const isThisGuestPage =
    url.origin === window.location.origin &&
    url.pathname === window.location.pathname
  return isThisGuestPage ? parseGuestLinkFragment(url.hash) : null
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
