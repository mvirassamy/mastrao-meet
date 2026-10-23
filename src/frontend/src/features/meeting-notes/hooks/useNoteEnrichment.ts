import { useEffect, useMemo, useSyncExternalStore } from 'react'
import { createNoteEnrichmentApi } from '../api/noteEnrichmentApi'
import { NoteEnrichmentController } from '../model/NoteEnrichmentController'
import type { PersonalNotesContextValue } from './usePersonalNotes'

export function useNoteEnrichment(
  notes: PersonalNotesContextValue,
  meetingRef: string
) {
  const controller = useMemo(
    () =>
      new NoteEnrichmentController(
        createNoteEnrichmentApi(notes.endpoint, meetingRef),
        notes.controller,
        meetingRef
      ),
    [notes.controller, notes.endpoint, meetingRef]
  )
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot
  )
  useEffect(() => {
    controller.start()
    const refresh = () => void controller.refresh()
    window.addEventListener('focus', refresh)
    return () => {
      window.removeEventListener('focus', refresh)
      controller.stop()
    }
  }, [controller])
  return { ...state, controller }
}
