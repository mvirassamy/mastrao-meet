let capturedInvitation: string | null | undefined

export const consumeGuestInvitationFragment = () => {
  if (capturedInvitation !== undefined) return capturedInvitation
  const fragment = new URLSearchParams(window.location.hash.slice(1))
  const invite = fragment.get('invite')
  capturedInvitation = invite && invite.length <= 16384 ? invite : null
  window.history.replaceState(
    window.history.state,
    '',
    `${window.location.pathname}${window.location.search}`
  )
  return capturedInvitation
}
