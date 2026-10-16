import { useEffect, useMemo, useSyncExternalStore, type ReactNode } from 'react'
import { createPersonalNotesApi } from '../api/personalNotesApi'
import { PersonalNotesController } from '../model/PersonalNotesController'
import { PersonalNotesContext } from '../hooks/usePersonalNotes'

export interface PersonalNotesProviderProps {
  endpoint: string
  /** Change on author/session changes, including guest authority changes. */
  identityKey?: string | number | null
  children: ReactNode
}

/** Mount above tabs/panels. Changing endpoint or identity discards the old scope. */
export function PersonalNotesProvider({
  endpoint,
  identityKey,
  children,
}: PersonalNotesProviderProps) {
  const scope = useMemo(
    () => ({ endpoint, identityKey }),
    [endpoint, identityKey]
  )
  const controller = useMemo(
    () => new PersonalNotesController(createPersonalNotesApi(scope.endpoint)),
    [scope]
  )
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot
  )

  useEffect(() => {
    controller.start()
    return controller.stop
  }, [controller])

  useEffect(() => {
    if (!state.hasUnsavedChanges) return
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [state.hasUnsavedChanges])

  useEffect(() => {
    const online = () => {
      if (controller.getSnapshot().status === 'error') void controller.retry()
    }
    window.addEventListener('online', online)
    return () => window.removeEventListener('online', online)
  }, [controller])

  return (
    <PersonalNotesContext.Provider
      value={{
        ...state,
        endpoint,
        flush: controller.flush,
        retry: controller.retry,
        setContent: controller.setContent,
        setComposing: controller.setComposing,
        loadSavedVersion: controller.loadSavedVersion,
        keepDraft: controller.keepDraft,
      }}
    >
      {children}
    </PersonalNotesContext.Provider>
  )
}
