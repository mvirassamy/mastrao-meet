import { useEffect, useId } from 'react'
import { useTranslation } from 'react-i18next'
import { LockIcon } from '@/icons'
import { TextArea } from '@/primitives/TextArea'
import { css } from '@/styled-system/css'
import { usePersonalNotes } from '../hooks/usePersonalNotes'
import { useFillScrollArea } from '../hooks/useFillScrollArea'
import { PERSONAL_NOTE_LIMIT } from '../model/noteContent'
import { NoteSaveAlert, NoteSaveStatus } from './NoteSaveStatus'
import { notePageScroll } from './notePageScroll'

/** The remaining count only shows once it matters. */
const NEAR_LIMIT = PERSONAL_NOTE_LIMIT * 0.9

/**
 * A blank page: the text fills the space, status and privacy sit underneath.
 * `panel` fills the room side panel; `content` grows with the note, as on the
 * meeting page. A toolbar that already shows the save status hides it here.
 */
export function PersonalNotesEditor({
  fit = 'panel',
  showSaveStatus = true,
}: {
  fit?: 'panel' | 'content'
  showSaveStatus?: boolean
}) {
  const notes = usePersonalNotes()
  const { t } = useTranslation('personalNotes', { useSuspense: false })
  const id = useId()
  // On the meeting page the note takes the rest of the screen and scrolls itself.
  const page = useFillScrollArea<HTMLTextAreaElement>(
    fit === 'content' && Boolean(notes)
  )
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
      data-fit={fit}
      className={css({
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: '12rem',
        '&[data-fit=content]': { minHeight: 0 },
      })}
    >
      <NoteSaveAlert notes={notes} />
      {notes.wasRejected && (
        <p
          role="alert"
          className={css({
            marginX: '1rem',
            marginTop: '0.75rem',
            fontSize: '0.8125rem',
            color: 'warning-foreground',
          })}
        >
          {t('limitReached', {
            defaultValue:
              'Modification refusée : elle dépasse la limite de 100 000 caractères. Votre texte précédent est conservé.',
          })}
        </p>
      )}
      <label htmlFor={id} className={css({ srOnly: true })}>
        {t('title', { defaultValue: 'Mes notes' })}
      </label>
      <TextArea
        ref={page.ref}
        id={id}
        aria-describedby={[
          `${id}-privacy`,
          `${id}-limit`,
          notes.isGuest && `${id}-guest-notice`,
        ]
          .filter(Boolean)
          .join(' ')}
        className={fit === 'content' ? notePageScroll : undefined}
        style={{ height: page.height }}
        value={notes.content}
        disabled={!notes.hasLoaded}
        readOnly={notes.isMutationLocked}
        aria-busy={notes.isMutationLocked}
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
        // Through the css prop so it overrides the TextArea recipe border.
        css={{
          flex: '1',
          minHeight: '8rem',
          '[data-fit=content] &': { flex: 'none', minHeight: '5.5rem' },
          overflowY: 'auto',
          resize: 'none',
          border: 'none',
          borderRadius: 0,
          backgroundColor: 'transparent',
          paddingX: '1.25rem',
          paddingY: '1rem',
          fontSize: '0.875rem',
          lineHeight: '1.75',
          color: 'var(--heading-foreground)',
          whiteSpace: 'pre-wrap',
          _placeholder: { color: 'muted-foreground' },
          _disabled: { color: 'muted-foreground' },
          // A writing surface: the caret shows the focus, a frame would only
          // box the page. Important: the global focus ring is unlayered.
          _focusVisible: { outline: 'none!' },
        }}
      />
      <p id={`${id}-limit`} className={css({ srOnly: true })}>
        {t('limit', {
          defaultValue: '{{count}} caractères maximum.',
          count: PERSONAL_NOTE_LIMIT,
        })}
      </p>
      <footer
        className={css({
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '0.75rem',
          // Same inset hairline as the tab list above.
          marginX: '1.25rem',
          paddingY: '0.625rem',
          borderTop: '1px solid token(colors.border)',
          fontSize: '0.75rem',
          color: 'muted-foreground',
        })}
      >
        <span
          id={`${id}-privacy`}
          className={css({
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.375rem',
          })}
        >
          <LockIcon size={13} aria-hidden="true" />
          {t('private', { defaultValue: 'Visibles par vous seul' })}
        </span>
        <span
          className={css({
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.75rem',
          })}
        >
          {notes.length >= NEAR_LIMIT && (
            <span>
              {t('remaining', {
                defaultValue: '{{count}} caractères restants.',
                count: Math.max(0, PERSONAL_NOTE_LIMIT - notes.length),
              })}
            </span>
          )}
          {showSaveStatus && <NoteSaveStatus notes={notes} />}
        </span>
      </footer>
      {notes.isGuest && (
        <p
          id={`${id}-guest-notice`}
          className={css({
            marginX: '1.25rem',
            marginBottom: '0.75rem',
            padding: '0.625rem',
            borderRadius: '8px',
            backgroundColor: 'muted',
            color: 'muted-foreground',
            fontSize: '0.75rem',
            lineHeight: '1.5',
          })}
        >
          {t('guestNotice', {
            defaultValue:
              'Notes temporaires — pensez à les copier avant de quitter. Pour retrouver vos notes après vos réunions, utilisez un compte connecté.',
          })}
        </p>
      )}
    </section>
  )
}
