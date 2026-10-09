import { useRef, useState } from 'react'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import { authUrl } from '@/features/auth/utils/authUrl'

/**
 * Confirms host access to a canonical room, then opens it. A retry after a
 * lost response reuses the same idempotency key, kept for this tab.
 */
export const useHostHandoff = (
  roomRef: string,
  authenticationReturnTo?: string
) => {
  const storageKey = `mastrao-host-recovery:${roomRef}`
  const key = useRef<string>()
  const [pending, setPending] = useState(false)
  const [failed, setFailed] = useState(false)

  const getCommandKey = () => {
    if (key.current) return key.current
    try {
      key.current = sessionStorage.getItem(storageKey) || crypto.randomUUID()
    } catch {
      key.current = crypto.randomUUID()
    }
    return key.current
  }

  const handoff = async () => {
    if (pending) return
    setPending(true)
    setFailed(false)
    const commandKey = getCommandKey()
    try {
      sessionStorage.setItem(storageKey, commandKey)
    } catch {
      // The in-memory key still keeps retries idempotent in this tab.
    }
    try {
      const result = await fetchApi<{ room_url: string }>(
        `rooms/${encodeURIComponent(roomRef)}/host-handoff/`,
        {
          method: 'POST',
          headers: { 'X-Idempotency-Key': commandKey },
        }
      )
      if (result.room_url !== `/${roomRef}`)
        throw new Error('Host recovery binding mismatch')
      window.location.assign(result.room_url)
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 401) {
        window.location.assign(
          authUrl(
            authenticationReturnTo
              ? { returnTo: authenticationReturnTo }
              : undefined
          )
        )
        return
      }
      if (error instanceof ApiError && error.statusCode === 404) {
        key.current = crypto.randomUUID()
        try {
          sessionStorage.removeItem(storageKey)
        } catch {
          // The next retry uses the renewed in-memory key.
        }
      }
      setFailed(true)
    } finally {
      setPending(false)
    }
  }

  return { handoff, pending, failed }
}
