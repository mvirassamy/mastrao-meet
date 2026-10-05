import { useRef, useState } from 'react'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import { useUser } from '@/features/auth/api/useUser'
import { authUrl } from '@/features/auth/utils/authUrl'

export const useHostRecovery = (roomRef: string | undefined) => {
  const { isLoggedIn } = useUser()
  const storageKey = `mastrao-host-recovery:${roomRef}`
  const key = useRef<string>()
  const [pending, setPending] = useState(false)
  const [failed, setFailed] = useState(false)

  const recover = async () => {
    if (!roomRef || pending || isLoggedIn === undefined) return
    if (!isLoggedIn) {
      window.location.assign(
        authUrl({
          returnTo: new URL(`/host/${roomRef}`, window.location.origin).href,
        })
      )
      return
    }
    setPending(true)
    setFailed(false)
    try {
      key.current ??= sessionStorage.getItem(storageKey) || crypto.randomUUID()
      sessionStorage.setItem(storageKey, key.current)
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
        window.location.assign(
          authUrl({
            returnTo: new URL(`/host/${roomRef}`, window.location.origin).href,
          })
        )
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

  return {
    recover,
    pending,
    failed,
    isDisabled: pending || isLoggedIn === undefined,
  }
}
