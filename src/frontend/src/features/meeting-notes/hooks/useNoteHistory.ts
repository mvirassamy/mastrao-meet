import { useEffect, useState } from 'react'
import { NoteHistory } from '../model/NoteHistory'
import type { PersonalNotesContextValue } from './usePersonalNotes'

/** A typing pause this long closes one step of history. */
export const HISTORY_STEP_PAUSE_MS = 700

/** Undo and redo for the loaded note; each step back saves like typing. */
export function useNoteHistory(notes: PersonalNotesContextValue) {
  const [history] = useState(() => new NoteHistory(notes.content))
  const { content, setContent, isMutationLocked } = notes

  useEffect(() => {
    const timer = setTimeout(
      () => history.record(content),
      HISTORY_STEP_PAUSE_MS
    )
    return () => clearTimeout(timer)
  }, [history, content])

  const move = (version: string | null) => {
    if (version !== null) setContent(version)
  }

  return {
    canUndo: !isMutationLocked && history.canUndo(content),
    canRedo: !isMutationLocked && history.canRedo(content),
    undo: () => move(history.undo(content)),
    redo: () => move(history.redo(content)),
  }
}
