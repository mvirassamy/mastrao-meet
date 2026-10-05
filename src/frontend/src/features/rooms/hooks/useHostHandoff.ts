import { useRef, useState } from 'react'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import { authUrl } from '@/features/auth/utils/authUrl'

/**
 * Confirms host access to a canonical room, then opens it. A retry after a
 * lost response reuses the same idempotency key, kept for this tab.
 */
export const useHostHandoff = (roomRef: string) => {
  const storageKey = `mastrao-host-recovery:${roomRef}`
  const key = useRef(sessionStorage.getItem(storageKey) || crypto.randomUUID())
  const [pending, setPending] = useState(false)
  const [failed, setFailed] = useState(false)

  const handoff = async () => {
    if (pending) return
    setPending(true)
    setFailed(false)
    sessionStorage.setItem(storageKey, key.current)
    try {
      const result = await fetchApi<{ room_url: string }>(
        `rooms/${encodeURIComponent(roomRef)}/host-handoff/`,
        {
          method: 'POST',
          headers: { 'X-Idempotency-Key': key.current },
        }
      )
      if (result.room_url !== `/${roomRef}`)
        throw new Error('Host recovery binding mismatch')
      window.location.assign(result.room_url)
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 401) {
        window.location.assign(authUrl())
        return
      }
      if (error instanceof ApiError && error.statusCode === 404) {
        key.current = crypto.randomUUID()
        sessionStorage.removeItem(storageKey)
      }
      setFailed(true)
    } finally {
      setPending(false)
    }
  }

  return { handoff, pending, failed }
}
