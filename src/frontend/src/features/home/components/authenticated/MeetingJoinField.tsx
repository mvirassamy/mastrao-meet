import { FormEvent, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyboardIcon } from '@/icons'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import { navigateTo } from '@/navigation/navigateTo'
import {
  isMeetingInputValid,
  parseMeetingInput,
} from '../../utils/meetingJoinInput'

export const MeetingJoinField = () => {
  const { t } = useTranslation('home')
  const [value, setValue] = useState('')
  const [showError, setShowError] = useState(false)
  const valid = isMeetingInputValid(value)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const roomId = parseMeetingInput(value)
    if (!roomId) {
      setShowError(value.trim().length > 0)
      return
    }
    navigateTo('room', roomId)
  }

  return (
    <form
      onSubmit={submit}
      className={css({
        display: 'flex',
        alignItems: 'flex-start',
        gap: '0.5rem',
        minWidth: 0,
        width: { base: '100%', md: 'auto' },
      })}
    >
      <div
        className={css({
          minWidth: 0,
          width: { base: '100%', md: '23rem' },
        })}
      >
        <label
          htmlFor="authenticated-meeting-code"
          className={css({ srOnly: true })}
        >
          {t('dashboard.join.label')}
        </label>
        <div
          className={css({
            display: 'flex',
            alignItems: 'center',
            gap: '0.5rem',
            minHeight: { base: '44px', md: '38px' },
            paddingX: '0.625rem',
            // Same field look as the meeting chat input.
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
          <KeyboardIcon size={16} aria-hidden="true" />
          <input
            id="authenticated-meeting-code"
            value={value}
            onChange={(event) => {
              setValue(event.currentTarget.value)
              setShowError(false)
            }}
            onBlur={() => setShowError(value.trim().length > 0 && !valid)}
            placeholder={t('dashboard.join.placeholder')}
            aria-invalid={showError}
            aria-describedby={showError ? 'meeting-code-error' : undefined}
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
            id="meeting-code-error"
            role="alert"
            className={css({
              color: 'danger',
              fontSize: '0.75rem',
              marginTop: '0.25rem',
              paddingLeft: '1rem',
            })}
          >
            {t('dashboard.join.error')}
          </p>
        )}
      </div>
      <Button
        type="submit"
        variant="ghost"
        isDisabled={!valid}
        className={css({ minHeight: { base: '44px', md: '38px' } })}
      >
        {t('dashboard.join.submit')}
      </Button>
    </form>
  )
}
