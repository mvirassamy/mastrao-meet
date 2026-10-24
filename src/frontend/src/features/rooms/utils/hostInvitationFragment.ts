let capturedInvitation: string | null | undefined

export const consumeHostInvitationFragment = () => {
  if (capturedInvitation !== undefined) return capturedInvitation
  const fragment = new URLSearchParams(window.location.hash.slice(1))
  const keys = [...fragment.keys()]
  const parameter = keys[0]
  // Core issues host_invitation; existing host links remain redeemable.
  const knownParameter = parameter === 'host_invitation' || parameter === 'host'
  const invitation =
    keys.length === 1 && knownParameter ? fragment.get(parameter) : null
  capturedInvitation =
    invitation && invitation.length <= 16384 ? invitation : null
  window.history.replaceState(
    window.history.state,
    '',
    `${window.location.pathname}${window.location.search}`
  )
  return capturedInvitation
}
