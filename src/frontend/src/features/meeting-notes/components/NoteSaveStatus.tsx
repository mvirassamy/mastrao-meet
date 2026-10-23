import { useTranslation } from 'react-i18next'
import { WarningIcon } from '@/icons'
import { Button } from '@/primitives/Button'
import { css } from '@/styled-system/css'
import type { PersonalNotesContextValue } from '../hooks/usePersonalNotes'

type Translate = ReturnType<typeof useTranslation>['t']
type Tone = 'saved' | 'temporary' | 'busy' | 'problem'

/** Short label and tone shown in the editor footer. */
function statusLine(
  notes: PersonalNotesContextValue,
  t: Translate
): [string, Tone] {
  if (notes.isMutationLocked) {
    return [
      t('enrichment.verifying', {
        defaultValue: 'Vérification de l’enregistrement…',
      }),
      'busy',
    ]
  }
  switch (notes.status) {
    case 'loading':
      return [t('loading', { defaultValue: 'Chargement des notes…' }), 'busy']
    case 'ready':
      if (notes.isGuest) {
        return [
          t('temporary', { defaultValue: 'Notes temporaires' }),
          'temporary',
        ]
      }
      return [t('saved', { defaultValue: 'Enregistré' }), 'saved']
    case 'pending':
    case 'saving':
      return [t('saving', { defaultValue: 'Enregistrement…' }), 'busy']
    case 'error':
    case 'unauthorized':
      if (!notes.hasUnsavedChanges) {
        return [
          t('loadFailed', { defaultValue: 'Notes non chargées' }),
          'problem',
        ]
      }
      return [t('notSaved', { defaultValue: 'Non enregistré' }), 'problem']
    case 'conflict':
      return [t('notSaved', { defaultValue: 'Non enregistré' }), 'problem']
  }
}

/** Explanation of a problem that needs the user, shown above the text. */
function problemMessage(notes: PersonalNotesContextValue, t: Translate) {
  switch (notes.status) {
    case 'error':
      if (!notes.hasUnsavedChanges) {
        return t('loadError', {
          defaultValue: 'Impossible de charger les notes. Réessayez.',
        })
      }
      return t('saveError', {
        defaultValue:
          'Notes non enregistrées. Votre brouillon est conservé ici.',
      })
    case 'unauthorized':
      if (!notes.hasUnsavedChanges) {
        return t('accessError', {
          defaultValue:
            'Impossible d’accéder aux notes. Vérifiez votre session et vos droits d’accès, puis réessayez.',
        })
      }
      return t('accessErrorWithDraft', {
        defaultValue:
          'Impossible d’accéder aux notes. Votre brouillon est conservé ici. Vérifiez votre session et vos droits d’accès, puis réessayez.',
      })
    case 'conflict':
      return t('conflict', {
        defaultValue:
          'Une autre version a été enregistrée. Votre brouillon est conservé ici.',
      })
    case 'loading':
    case 'ready':
    case 'pending':
    case 'saving':
      return undefined
  }
}

/** Actions read as links aligned with the message text. */
const inlineAction = css({ paddingX: '0!', minHeight: 'auto!' })

const dotColors: Record<Tone, string> = {
  saved: 'var(--success-foreground)',
  temporary: 'var(--muted-foreground)',
  busy: 'var(--input)',
  problem: 'var(--call-attention-badge)',
}

/** Live footer status; problems are explained by NoteSaveAlert. */
export function NoteSaveStatus({
  notes,
}: {
  notes: PersonalNotesContextValue
}) {
  const { t } = useTranslation('personalNotes', { useSuspense: false })
  const [label, tone] = statusLine(notes, t)
  return (
    <span
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className={css({
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.375rem',
        color:
          tone === 'problem'
            ? 'var(--call-attention-foreground)'
            : 'muted-foreground',
      })}
    >
      <span
        aria-hidden="true"
        className={css({ width: '7px', height: '7px', borderRadius: 'full' })}
        style={{ backgroundColor: dotColors[tone] }}
      />
      {label}
    </span>
  )
}

/** Failed loads keep editing disabled; existing drafts remain editable. */
export function NoteSaveAlert({ notes }: { notes: PersonalNotesContextValue }) {
  const { t } = useTranslation('personalNotes', { useSuspense: false })
  const problem = problemMessage(notes, t)
  const canRetry = notes.status === 'error' || notes.status === 'unauthorized'
  if (!problem && !notes.conflict) return null
  return (
    <div
      role="alert"
      className={css({
        display: 'flex',
        gap: '0.625rem',
        marginX: '1.25rem',
        marginTop: '1rem',
        padding: '0.75rem 0.875rem',
        borderRadius: '12px',
        fontSize: '0.8125rem',
        lineHeight: '1.5',
        color: 'foreground',
        // The call's attention palette, as on the muted mic and camera.
        backgroundColor:
          'color-mix(in srgb, var(--call-attention) 55%, var(--card))',
      })}
    >
      <WarningIcon
        size={16}
        aria-hidden="true"
        className={css({
          flexShrink: 0,
          marginTop: '2px',
          color: 'var(--call-attention-foreground)',
        })}
      />
      <div
        className={css({
          display: 'flex',
          flexDirection: 'column',
          gap: '0.25rem',
          alignItems: 'flex-start',
        })}
      >
        <p>{problem}</p>
        {canRetry && (
          <Button
            variant="link"
            className={inlineAction}
            size="xs"
            onPress={() => void notes.retry()}
          >
            {t('retry', { defaultValue: 'Réessayer' })}
          </Button>
        )}
        {notes.conflict && (
          <div
            className={css({
              display: 'flex',
              flexWrap: 'wrap',
              gap: '0.5rem',
            })}
          >
            <Button
              variant="link"
              className={inlineAction}
              size="xs"
              onPress={notes.loadSavedVersion}
            >
              {t('loadSaved', {
                defaultValue: 'Charger la version enregistrée',
              })}
            </Button>
            <Button
              variant="link"
              className={inlineAction}
              size="xs"
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
    </div>
  )
}
