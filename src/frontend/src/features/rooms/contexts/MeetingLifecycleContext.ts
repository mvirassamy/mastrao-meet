import { createContext, useContext } from 'react'

export type MeetingLifecycleContextValue = {
  phase: 'active' | 'requesting' | 'ending' | 'uncertain' | 'ended'
  isEnding: boolean
  closeRequestId?: string
  beginEnding: () => string
  markEnding: () => void
  markEndingUncertain: () => void
  markActive: () => void
  markEnded: () => void
}

export const MeetingLifecycleContext =
  createContext<MeetingLifecycleContextValue>({
    phase: 'active',
    isEnding: false,
    beginEnding: () => '',
    markEnding: () => undefined,
    markEndingUncertain: () => undefined,
    markActive: () => undefined,
    markEnded: () => undefined,
  })

export const useMeetingLifecycle = () => useContext(MeetingLifecycleContext)
