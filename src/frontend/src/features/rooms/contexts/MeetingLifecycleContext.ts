import { createContext, useContext } from 'react'
import type { RoomLifecycle } from '../api/fetchRoomLifecycle'

export type MeetingLifecycleContextValue = {
  phase: 'active' | 'requesting' | 'ending' | 'uncertain' | 'ended'
  canonicalLifecycle: RoomLifecycle | null
  reconcileLifecycle: () => void
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
    canonicalLifecycle: null,
    reconcileLifecycle: () => undefined,
    isEnding: false,
    beginEnding: () => '',
    markEnding: () => undefined,
    markEndingUncertain: () => undefined,
    markActive: () => undefined,
    markEnded: () => undefined,
  })

export const useMeetingLifecycle = () => useContext(MeetingLifecycleContext)
