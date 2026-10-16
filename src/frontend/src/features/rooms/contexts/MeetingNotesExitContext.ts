import { createContext, useContext } from 'react'

export type MeetingNotesExit = {
  requestExit: (action: () => void) => void
  isSaving: boolean
}

export const MeetingNotesExitContext = createContext<MeetingNotesExit | null>(
  null
)
export const useMeetingNotesExit = () => useContext(MeetingNotesExitContext)
