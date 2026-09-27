import { FormEvent, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { RiKeyboardBoxFill } from '@remixicon/react'
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
            border: '1px solid',
            borderColor: showError ? 'danger' : 'input',
            borderRadius: 'control',
            backgroundColor: 'card',
            color: 'muted-foreground',
            _focusWithin: {
              borderColor: 'primary',
              boxShadow: '0 0 0 2px token(colors.ring)',
            },
          })}
        >
          <RiKeyboardBoxFill size={16} aria-hidden="true" />
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
        size="app"
        variant="secondaryText"
        isDisabled={!valid}
        className={css({ minHeight: { base: '44px', md: '38px' } })}
      >
        {t('dashboard.join.submit')}
      </Button>
    </form>
  )
}
