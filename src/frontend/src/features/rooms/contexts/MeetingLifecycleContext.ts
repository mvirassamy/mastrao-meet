import { createContext, useContext } from 'react'

export type MeetingLifecycleContextValue = {
  isEnding: boolean
  markEnding: () => void
}

export const MeetingLifecycleContext =
  createContext<MeetingLifecycleContextValue>({
    isEnding: false,
    markEnding: () => undefined,
  })

export const useMeetingLifecycle = () => useContext(MeetingLifecycleContext)
