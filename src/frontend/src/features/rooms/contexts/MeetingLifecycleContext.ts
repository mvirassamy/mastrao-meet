import { createContext, useContext } from 'react'

export type MeetingLifecycleContextValue = {
  phase: 'active' | 'requesting' | 'uncertain' | 'ended'
  isEnding: boolean
  beginEnding: () => void
  markEndingUncertain: () => void
  markEnded: () => void
}

export const MeetingLifecycleContext =
  createContext<MeetingLifecycleContextValue>({
    phase: 'active',
    isEnding: false,
    beginEnding: () => undefined,
    markEndingUncertain: () => undefined,
    markEnded: () => undefined,
  })

export const useMeetingLifecycle = () => useContext(MeetingLifecycleContext)
