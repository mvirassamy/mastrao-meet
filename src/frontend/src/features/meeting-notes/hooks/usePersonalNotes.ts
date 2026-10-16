import { createContext, useContext } from 'react'
import type {
  PersonalNotesController,
  PersonalNotesState,
} from '../model/PersonalNotesController'

export interface PersonalNotesContextValue extends PersonalNotesState {
  endpoint: string
  flush: PersonalNotesController['flush']
  retry: PersonalNotesController['retry']
  setContent: PersonalNotesController['setContent']
  setComposing: PersonalNotesController['setComposing']
  loadSavedVersion: PersonalNotesController['loadSavedVersion']
  keepDraft: PersonalNotesController['keepDraft']
}

export const PersonalNotesContext =
  createContext<PersonalNotesContextValue | null>(null)

/** Optional so leave controls can also render outside notes-enabled meetings. */
export const usePersonalNotes = () => useContext(PersonalNotesContext)
