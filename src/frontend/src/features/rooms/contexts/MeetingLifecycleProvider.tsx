import { type ReactNode, useCallback, useMemo, useState } from 'react'

import { MeetingLifecycleContext } from './MeetingLifecycleContext'

export const MeetingLifecycleProvider = ({
  children,
}: {
  children: ReactNode
}) => {
  const [isEnding, setIsEnding] = useState(false)
  const markEnding = useCallback(() => setIsEnding(true), [])
  const value = useMemo(
    () => ({ isEnding, markEnding }),
    [isEnding, markEnding]
  )

  return (
    <MeetingLifecycleContext.Provider value={value}>
      {children}
    </MeetingLifecycleContext.Provider>
  )
}
