import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import { MeetingLifecycleContext } from './MeetingLifecycleContext'
import { endMeeting } from '../api/endMeeting'

const RESUME_RETRY_MS = 5_000
const RESUME_TIMEOUT_MS = 15_000

const readStoredCloseRequestId = (storageKey: string) => {
  try {
    return window.sessionStorage.getItem(storageKey) || undefined
  } catch {
    return undefined
  }
}

const writeStoredCloseRequestId = (storageKey: string, requestId: string) => {
  try {
    window.sessionStorage.setItem(storageKey, requestId)
  } catch {
    // Storage is only a reload aid. The in-memory request id remains authoritative
    // for the current tab and the close request must still be sent.
  }
}

const clearStoredCloseRequestId = (storageKey: string) => {
  try {
    window.sessionStorage.removeItem(storageKey)
  } catch {
    // Best-effort cleanup.
  }
}

export const MeetingLifecycleProvider = ({
  children,
  roomId,
}: {
  children: ReactNode
  roomId: string
}) => {
  const storageKey = `mastrao-meeting-close-v1:${roomId}`
  const [closeRequestId, setCloseRequestId] = useState<string | undefined>(() =>
    readStoredCloseRequestId(storageKey)
  )
  const [phase, setPhase] = useState<
    'active' | 'requesting' | 'ending' | 'uncertain' | 'ended'
  >(() => (readStoredCloseRequestId(storageKey) ? 'uncertain' : 'active'))
  const closeRequestIdRef = useRef(closeRequestId)
  const restoredCloseRequestIdRef = useRef(closeRequestId)

  useEffect(() => {
    const requestId = restoredCloseRequestIdRef.current
    if (!requestId) return

    let cancelled = false
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let controller: AbortController | undefined
    const resume = async () => {
      controller = new AbortController()
      const timeout = window.setTimeout(
        () => controller?.abort(),
        RESUME_TIMEOUT_MS
      )
      try {
        await endMeeting(roomId, requestId, controller.signal)
        if (!cancelled) setPhase('ending')
      } catch {
        if (!cancelled) {
          setPhase('uncertain')
          retryTimer = setTimeout(resume, RESUME_RETRY_MS)
        }
      } finally {
        window.clearTimeout(timeout)
      }
    }
    void resume()
    return () => {
      cancelled = true
      controller?.abort()
      if (retryTimer) clearTimeout(retryTimer)
    }
  }, [roomId])

  const beginEnding = useCallback(() => {
    const requestId =
      closeRequestIdRef.current ??
      `close_${crypto.randomUUID().replaceAll('-', '')}`
    closeRequestIdRef.current = requestId
    writeStoredCloseRequestId(storageKey, requestId)
    setCloseRequestId(requestId)
    setPhase('requesting')
    return requestId
  }, [storageKey])
  const markEnding = useCallback(() => setPhase('ending'), [])
  const markEndingUncertain = useCallback(() => setPhase('uncertain'), [])
  const clear = useCallback(() => {
    clearStoredCloseRequestId(storageKey)
    closeRequestIdRef.current = undefined
    setCloseRequestId(undefined)
  }, [storageKey])
  const markActive = useCallback(() => {
    clear()
    setPhase('active')
  }, [clear])
  const markEnded = useCallback(() => {
    clear()
    setPhase('ended')
  }, [clear])
  const value = useMemo(
    () => ({
      phase,
      isEnding: phase !== 'active',
      closeRequestId,
      beginEnding,
      markEnding,
      markEndingUncertain,
      markActive,
      markEnded,
    }),
    [
      beginEnding,
      closeRequestId,
      markActive,
      markEnded,
      markEnding,
      markEndingUncertain,
      phase,
    ]
  )

  return (
    <MeetingLifecycleContext.Provider value={value}>
      {children}
    </MeetingLifecycleContext.Provider>
  )
}
