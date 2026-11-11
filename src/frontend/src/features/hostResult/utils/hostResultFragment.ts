let capturedAccess: string | null | undefined

/** Read the link credential once, then remove it from the address bar and
 * history so it is not copied, bookmarked or exposed by a later navigation. */
export const consumeHostResultFragment = () => {
  if (capturedAccess !== undefined) return capturedAccess
  const fragment = new URLSearchParams(window.location.hash.slice(1))
  const keys = [...fragment.keys()]
  const access =
    keys.length === 1 && keys[0] === 'acces' ? fragment.get('acces') : null
  capturedAccess = access && access.length <= 16384 ? access : null
  window.history.replaceState(
    window.history.state,
    '',
    `${window.location.pathname}${window.location.search}`
  )
  return capturedAccess
}
