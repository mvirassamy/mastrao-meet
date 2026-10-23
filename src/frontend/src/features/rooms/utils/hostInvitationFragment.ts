let capturedInvitation: string | null | undefined

export const consumeHostInvitationFragment = () => {
  if (capturedInvitation !== undefined) return capturedInvitation
  const fragment = new URLSearchParams(window.location.hash.slice(1))
  const invitation = fragment.get('host')
  capturedInvitation =
    invitation && invitation.length <= 16384 ? invitation : null
  window.history.replaceState(
    window.history.state,
    '',
    `${window.location.pathname}${window.location.search}`
  )
  return capturedInvitation
}
