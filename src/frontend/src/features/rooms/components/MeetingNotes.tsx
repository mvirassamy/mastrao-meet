import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  PersonalNotesEditor,
  PersonalNotesProvider,
  usePersonalNotes,
} from '@/features/meeting-notes'
import { Button, Dialog, DialogActions, P } from '@/primitives'
import { MeetingNotesExitContext } from '../contexts/MeetingNotesExitContext'
import { Router } from 'wouter'
import { useNavigationGuard } from '@/navigation/useNavigationGuard'

/** Keeps the draft alive when an imposed disconnect races a pending save. */
function NotesExitGuard({ children }: { children: ReactNode }) {
  const notes = usePersonalNotes()
  const { t } = useTranslation('personalNotes', { useSuspense: false })
  const pendingExit = useRef<(() => void) | null>(null)
  const saving = useRef(false)
  const active = useRef(true)
  const [isSaving, setIsSaving] = useState(false)
  const [needsRecovery, setNeedsRecovery] = useState(false)
  const flush = notes?.flush
  const retry = notes?.retry
  const hasUnsavedChanges = notes?.hasUnsavedChanges ?? false

  useEffect(() => {
    active.current = true
    return () => {
      active.current = false
    }
  }, [])

  const finishExit = useCallback(() => {
    const action = pendingExit.current
    pendingExit.current = null
    setNeedsRecovery(false)
    action?.()
  }, [])

  const saveAndExit = useCallback(
    async (recover = false) => {
      if (saving.current) return
      saving.current = true
      setIsSaving(true)
      let saved = false
      try {
        const save = recover ? retry : flush
        saved = !save || (await save())
      } catch {
        /* Keep the draft available for recovery. */
      }
      if (!active.current) return
      saving.current = false
      setIsSaving(false)
      if (saved) finishExit()
      else setNeedsRecovery(true)
    },
    [finishExit, flush, retry]
  )

  const requestExit = useCallback(
    (action: () => void) => {
      if (pendingExit.current) return
      if (!hasUnsavedChanges) {
        action()
        return
      }
      pendingExit.current = action
      void saveAndExit()
    },
    [hasUnsavedChanges, saveAndExit]
  )

  useNavigationGuard(requestExit)

  return (
    <MeetingNotesExitContext.Provider value={{ requestExit, isSaving }}>
      <Router
        aroundNav={(navigate, path, options) =>
          requestExit(() => navigate(path, options))
        }
      >
        {children}
      </Router>
      <Dialog
        isOpen={needsRecovery || isSaving}
        role="alertdialog"
        title={needsRecovery ? t('exit.title') : t('saving')}
        aria-label={needsRecovery ? t('exit.title') : t('saving')}
      >
        <P>{needsRecovery ? t('exit.body') : t('saving')}</P>
        <PersonalNotesEditor />
        <DialogActions>
          <Button variant="outline" isDisabled={isSaving} onPress={finishExit}>
            {t('exit.discard')}
          </Button>
          <Button
            isDisabled={isSaving}
            loading={isSaving}
            onPress={() => void saveAndExit(true)}
          >
            {t('exit.retry')}
          </Button>
        </DialogActions>
      </Dialog>
    </MeetingNotesExitContext.Provider>
  )
}

export function MeetingNotes({
  roomId,
  identityKey,
  children,
}: {
  roomId: string
  identityKey: string
  children: ReactNode
}) {
  return (
    <MeetingNotesScope
      endpoint={`/rooms/${encodeURIComponent(roomId)}/personal-note/`}
      identityKey={identityKey}
    >
      {children}
    </MeetingNotesScope>
  )
}

export function MeetingNotesScope({
  endpoint,
  identityKey,
  children,
}: {
  endpoint: string
  identityKey: string
  children: ReactNode
}) {
  return (
    <PersonalNotesProvider endpoint={endpoint} identityKey={identityKey}>
      <NotesExitGuard key={`${endpoint}:${identityKey}`}>
        {children}
      </NotesExitGuard>
    </PersonalNotesProvider>
  )
}
