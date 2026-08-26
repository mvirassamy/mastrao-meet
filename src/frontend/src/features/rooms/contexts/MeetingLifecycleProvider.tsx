import { type ReactNode, useCallback, useMemo, useState } from 'react'

import { MeetingLifecycleContext } from './MeetingLifecycleContext'

export const MeetingLifecycleProvider = ({
  children,
}: {
  children: ReactNode
}) => {
  const [phase, setPhase] = useState<
    'active' | 'requesting' | 'uncertain' | 'ended'
  >('active')
  const beginEnding = useCallback(() => setPhase('requesting'), [])
  const markEndingUncertain = useCallback(() => setPhase('uncertain'), [])
  const markEnded = useCallback(() => setPhase('ended'), [])
  const value = useMemo(
    () => ({
      phase,
      isEnding: phase !== 'active',
      beginEnding,
      markEndingUncertain,
      markEnded,
    }),
    [beginEnding, markEnded, markEndingUncertain, phase]
  )

  return (
    <MeetingLifecycleContext.Provider value={value}>
      {children}
    </MeetingLifecycleContext.Provider>
  )
}
