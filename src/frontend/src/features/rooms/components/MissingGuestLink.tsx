import { FormEvent, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { LinkIcon } from '@/icons'
import { Button, H, Text } from '@/primitives'
import { css } from '@/styled-system/css'
import {
  type GuestLink,
  parsePastedGuestLink,
} from '../utils/guestInvitationFragment'

/**
 * Shown when the guest page was opened without a usable link: the guest
 * pastes the complete link from the invitation e-mail and joins from here.
 */
export const MissingGuestLink = ({
  onLink,
}: {
  onLink: (link: GuestLink) => void
}) => {
  const { t } = useTranslation()
  const inputId = useId()
  const errorId = useId()
  const [value, setValue] = useState('')
  const [showError, setShowError] = useState(false)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const link = parsePastedGuestLink(value)
    if (!link) {
      setShowError(true)
      return
    }
    onLink(link)
  }

  return (
    <>
      <H lvl={1} margin={false} centered>
        {t('guestInvitation.missing.title')}
      </H>
      <Text as="p" variant="note">
        {t('guestInvitation.missing.body')}
      </Text>
      <form
        onSubmit={submit}
        className={css({
          display: 'flex',
          alignItems: 'flex-start',
          gap: '0.5rem',
          width: '100%',
          textAlign: 'left',
        })}
      >
        <div className={css({ flex: 1, minWidth: 0 })}>
          <label htmlFor={inputId} className={css({ srOnly: true })}>
            {t('guestInvitation.missing.label')}
          </label>
          <div
            className={css({
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
              minHeight: { base: '44px', md: '38px' },
              paddingX: '0.625rem',
              // Same field look as the home "code or link" field.
              border: '1px solid token(colors.border)',
              borderRadius: '8px',
              backgroundColor: 'card',
              boxShadow: '0 1px 2px rgb(0 0 0 / 0.05)',
              color: 'muted-foreground',
              transition: 'border-color 150ms, box-shadow 150ms',
              _hover: { borderColor: 'input' },
              _focusWithin: {
                borderColor: 'ring',
                boxShadow: '0 0 0 3px rgb(45 91 227 / 0.2)',
              },
              '&[data-invalid=true]': {
                borderColor: 'danger',
                boxShadow: '0 0 0 3px rgb(169 46 54 / 0.15)',
              },
            })}
            data-invalid={showError}
          >
            <LinkIcon size={16} aria-hidden="true" />
            <input
              id={inputId}
              value={value}
              onChange={(event) => {
                setValue(event.currentTarget.value)
                setShowError(false)
              }}
              placeholder={t('guestInvitation.missing.placeholder')}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={showError}
              aria-describedby={showError ? errorId : undefined}
              className={css({
                width: '100%',
                minWidth: 0,
                border: 0,
                outline: 0,
                background: 'transparent',
                color: 'foreground',
                fontSize: '0.875rem',
                lineHeight: '1.25rem',
                _placeholder: { color: 'muted-foreground' },
              })}
            />
          </div>
          {showError && (
            <p
              id={errorId}
              role="alert"
              className={css({
                color: 'danger',
                fontSize: '0.75rem',
                marginTop: '0.25rem',
                paddingLeft: '0.625rem',
              })}
            >
              {t('guestInvitation.missing.error')}
            </p>
          )}
        </div>
        <Button
          type="submit"
          isDisabled={!value.trim()}
          className={css({ minHeight: { base: '44px', md: '38px' } })}
        >
          {t('guestInvitation.missing.submit')}
        </Button>
      </form>
      <Text
        as="p"
        variant="note"
        margin={false}
        className={css({ fontSize: '0.8125rem' })}
      >
        {t('guestInvitation.missing.help')}
      </Text>
    </>
  )
}
