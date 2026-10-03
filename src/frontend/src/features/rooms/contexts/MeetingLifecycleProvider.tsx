import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import { MeetingLifecycleContext } from './MeetingLifecycleContext'
import { endMeeting, isRetryableEndMeetingError } from '../api/endMeeting'
import {
  fetchRoomLifecycle,
  type RoomLifecycle,
} from '../api/fetchRoomLifecycle'
import { isMissingRoomLifecycle } from '../api/isMissingRoomLifecycle'
import { isMastraoRoomId } from '../utils/isRoomValid'

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
  const [localPhase, setPhase] = useState<
    'active' | 'requesting' | 'ending' | 'uncertain' | 'ended'
  >(() => (readStoredCloseRequestId(storageKey) ? 'uncertain' : 'active'))
  const closeRequestIdRef = useRef(closeRequestId)
  const [canonicalLifecycle, setCanonicalLifecycle] =
    useState<RoomLifecycle | null>(null)
  // A refused local command cannot reopen a meeting the authority is closing.
  const phase =
    localPhase === 'active' && canonicalLifecycle?.state === 'ending'
      ? 'ending'
      : localPhase
  const [observationRequested, setObservationRequested] = useState(false)
  const reconcileLifecycle = useCallback(() => {
    setCanonicalLifecycle((current) =>
      current?.state === 'ending' ? current : null
    )
    setObservationRequested(true)
  }, [])

  const clear = useCallback(() => {
    clearStoredCloseRequestId(storageKey)
    closeRequestIdRef.current = undefined
    setCloseRequestId(undefined)
  }, [storageKey])
  const markActive = useCallback(() => {
    clear()
    setPhase((current) => (current === 'ended' ? current : 'active'))
  }, [clear])

  useEffect(() => {
    const requestId = closeRequestId
    if (phase !== 'uncertain' || !requestId) return

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
        if (!cancelled)
          setPhase((current) => (current === 'ended' ? current : 'ending'))
      } catch (error) {
        if (!isRetryableEndMeetingError(error)) {
          if (!cancelled) markActive()
          return
        }
        if (!cancelled) {
          setPhase((current) => (current === 'ended' ? current : 'uncertain'))
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
  }, [closeRequestId, markActive, phase, roomId])

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
  const markEnding = useCallback(
    () => setPhase((current) => (current === 'ended' ? current : 'ending')),
    []
  )
  const markEndingUncertain = useCallback(
    () => setPhase((current) => (current === 'ended' ? current : 'uncertain')),
    []
  )
  const markEnded = useCallback(() => {
    clear()
    setPhase('ended')
  }, [clear])
  const shouldObserve =
    isMastraoRoomId(roomId) &&
    phase !== 'ended' &&
    (observationRequested || phase === 'ending' || phase === 'uncertain')

  useEffect(() => {
    if (!shouldObserve) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const controller = new AbortController()
    const reconcile = async () => {
      try {
        const lifecycle = await fetchRoomLifecycle(roomId, controller.signal)
        if (cancelled) return
        setCanonicalLifecycle(lifecycle)
        switch (lifecycle.state) {
          case 'ended':
            markEnded()
            return
          case 'open':
            if (!closeRequestIdRef.current) {
              setObservationRequested(false)
              markActive()
              return
            }
            break
          case 'ending':
            // Observation must not cancel an initial command or its retry.
            if (!closeRequestIdRef.current) markEnding()
            break
        }
      } catch (error) {
        if (cancelled) return
        if (isMissingRoomLifecycle(error)) {
          setCanonicalLifecycle({ state: 'ended' })
          markEnded()
          return
        }
        // An unavailable authority does not invalidate its last known state.
      }
      timer = setTimeout(reconcile, 1000)
    }
    void reconcile()
    return () => {
      cancelled = true
      controller.abort()
      if (timer) clearTimeout(timer)
    }
  }, [markActive, markEnded, markEnding, roomId, shouldObserve])

  const value = useMemo(
    () => ({
      phase,
      canonicalLifecycle,
      reconcileLifecycle,
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
      canonicalLifecycle,
      reconcileLifecycle,
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
