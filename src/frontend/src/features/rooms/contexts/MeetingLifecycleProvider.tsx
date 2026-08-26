import { type ReactNode, useCallback, useMemo, useState } from 'react'

import { MeetingLifecycleContext } from './MeetingLifecycleContext'

export const MeetingLifecycleProvider = ({
  children,
  roomId,
}: {
  children: ReactNode
  roomId: string
}) => {
  const storageKey = `mastrao-meeting-close-v1:${roomId}`
  const [closeRequestId, setCloseRequestId] = useState<string | undefined>(
    () => {
      const stored = window.sessionStorage.getItem(storageKey)
      return stored || undefined
    }
  )
  const [phase, setPhase] = useState<
    'active' | 'requesting' | 'ending' | 'uncertain' | 'ended'
  >(() => (window.sessionStorage.getItem(storageKey) ? 'uncertain' : 'active'))
  const beginEnding = useCallback(() => {
    const requestId =
      closeRequestId ?? `close_${crypto.randomUUID().replaceAll('-', '')}`
    window.sessionStorage.setItem(storageKey, requestId)
    setCloseRequestId(requestId)
    setPhase('requesting')
    return requestId
  }, [closeRequestId, storageKey])
  const markEnding = useCallback(() => setPhase('ending'), [])
  const markEndingUncertain = useCallback(() => setPhase('uncertain'), [])
  const clear = useCallback(() => {
    window.sessionStorage.removeItem(storageKey)
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
