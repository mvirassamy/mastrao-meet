import { useTranslation } from 'react-i18next'
import { Button } from '@/primitives/Button'
import { css } from '@/styled-system/css'
import type { NoteStatus } from '../model/PersonalNotesController'
import type { PersonalNotesContextValue } from '../hooks/usePersonalNotes'

function statusMessage(
  status: NoteStatus,
  t: ReturnType<typeof useTranslation>['t']
) {
  switch (status) {
    case 'loading':
      return t('loading', { defaultValue: 'Chargement des notes…' })
    case 'ready':
      return t('saved', { defaultValue: 'Enregistré' })
    case 'pending':
      return t('unsaved', { defaultValue: 'Modifications non enregistrées' })
    case 'saving':
      return t('saving', { defaultValue: 'Enregistrement…' })
    case 'error':
      return t('saveError', {
        defaultValue:
          'Notes non enregistrées. Votre brouillon est conservé ici.',
      })
    case 'unauthorized':
      return t('sessionExpired', {
        defaultValue:
          'Accès aux notes expiré. Reconnectez-vous pour enregistrer votre brouillon.',
      })
    case 'conflict':
      return t('conflict', {
        defaultValue:
          'Une autre version a été enregistrée. Votre brouillon est conservé ici.',
      })
  }
}

export function NoteSaveStatus({
  notes,
}: {
  notes: PersonalNotesContextValue
}) {
  const { t } = useTranslation('personalNotes', { useSuspense: false })
  const hasError = notes.status === 'error' || notes.status === 'unauthorized'
  return (
    <div
      className={css({
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
      })}
    >
      <p role="status" aria-live="polite" aria-atomic="true">
        {statusMessage(notes.status, t)}
      </p>
      {hasError && (
        <Button variant="outline" size="sm" onPress={() => void notes.retry()}>
          {t('retry', { defaultValue: 'Réessayer' })}
        </Button>
      )}
      {notes.conflict && (
        <div
          className={css({ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' })}
        >
          <Button variant="outline" size="sm" onPress={notes.loadSavedVersion}>
            {t('loadSaved', { defaultValue: 'Charger la version enregistrée' })}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onPress={() => {
              if (
                window.confirm(
                  t('confirmOverwrite', {
                    defaultValue:
                      'Remplacer la version enregistrée par votre brouillon ?',
                  })
                )
              ) {
                void notes.keepDraft()
              }
            }}
          >
            {t('keepDraft', {
              defaultValue: 'Enregistrer mon brouillon à la place',
            })}
          </Button>
        </div>
      )}
    </div>
  )
}
