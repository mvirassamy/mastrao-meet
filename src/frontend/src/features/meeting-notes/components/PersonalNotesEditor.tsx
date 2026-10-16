import { useEffect, useId } from 'react'
import { useTranslation } from 'react-i18next'
import { TextArea } from '@/primitives/TextArea'
import { css } from '@/styled-system/css'
import { usePersonalNotes } from '../hooks/usePersonalNotes'
import { PERSONAL_NOTE_LIMIT } from '../model/noteContent'
import { NoteSaveStatus } from './NoteSaveStatus'

export function PersonalNotesEditor() {
  const notes = usePersonalNotes()
  const { t } = useTranslation('personalNotes', { useSuspense: false })
  const id = useId()
  const flush = notes?.flush
  const setComposing = notes?.setComposing

  useEffect(
    () => () => {
      setComposing?.(false)
      void flush?.()
    },
    [flush, setComposing]
  )

  if (!notes) return null

  return (
    <section
      aria-label={t('title', { defaultValue: 'Mes notes' })}
      className={css({
        display: 'flex',
        flexDirection: 'column',
        gap: '0.75rem',
        height: '100%',
        minHeight: 0,
        maxHeight: 'min(40rem, 70dvh)',
        overflowY: 'auto',
        padding: '1rem',
      })}
    >
      <p id={`${id}-privacy`}>
        {t('private', {
          defaultValue: 'Ces notes sont visibles uniquement par vous.',
        })}
      </p>
      <label htmlFor={id} className={css({ srOnly: true })}>
        {t('title', { defaultValue: 'Mes notes' })}
      </label>
      <TextArea
        id={id}
        aria-describedby={`${id}-privacy ${id}-limit`}
        value={notes.content}
        disabled={!notes.hasLoaded}
        placeholder={t('placeholder', {
          defaultValue: 'Notez vos idées pendant la réunion…',
        })}
        onChange={(event) => notes.setContent(event.currentTarget.value)}
        onCompositionStart={() => notes.setComposing(true)}
        onCompositionEnd={(event) => {
          notes.setContent(event.currentTarget.value)
          notes.setComposing(false)
        }}
        onBlur={() => {
          notes.setComposing(false)
          void notes.flush()
        }}
        className={css({
          flex: '1',
          minHeight: '8rem',
          maxHeight: '45dvh',
          overflowY: 'auto',
          resize: 'vertical',
          padding: '0.75rem',
          lineHeight: '1.6',
          whiteSpace: 'pre-wrap',
          _focusVisible: {
            outline: '2px solid',
            outlineColor: 'currentColor',
            outlineOffset: '2px',
          },
        })}
      />
      <p id={`${id}-limit`}>
        {t('limit', {
          defaultValue: '{{count}} caractères maximum.',
          count: PERSONAL_NOTE_LIMIT,
        })}
        {notes.length >= PERSONAL_NOTE_LIMIT * 0.9 && (
          <span>
            {' '}
            {t('remaining', {
              defaultValue: '{{count}} caractères restants.',
              count: Math.max(0, PERSONAL_NOTE_LIMIT - notes.length),
            })}
          </span>
        )}
      </p>
      {notes.wasRejected && (
        <p role="alert">
          {t('limitReached', {
            defaultValue:
              'Modification refusée : elle dépasse la limite de 100 000 caractères. Votre texte précédent est conservé.',
          })}
        </p>
      )}
      <NoteSaveStatus notes={notes} />
    </section>
  )
}
