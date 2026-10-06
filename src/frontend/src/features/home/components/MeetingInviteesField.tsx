import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { CloseIcon } from '@/icons'
import { Button, Field, Text } from '@/primitives'
import { css } from '@/styled-system/css'
import { inviteeEmailParts, readInviteeEmails } from '../utils/inviteeEmails'

export const MeetingInviteesField = ({
  value,
  onChange,
  error,
  isDisabled,
}: {
  value: string
  onChange: (value: string) => void
  error?: string
  isDisabled: boolean
}) => {
  const { t } = useTranslation('home', { keyPrefix: 'scheduleMeetingDialog' })
  const errorId = useId()
  const { emails } = readInviteeEmails(value)
  const remove = (email: string) =>
    onChange(
      inviteeEmailParts(value)
        .filter((part) => part.toLowerCase() !== email)
        .join(', ')
    )

  return (
    <div>
      <Field
        type="text"
        name="invitees"
        label={t('invitees')}
        description={t('inviteesDescription')}
        placeholder={t('inviteesPlaceholder')}
        inputMode="email"
        value={value}
        onChange={onChange}
        isDisabled={isDisabled}
        isInvalid={!!error}
        aria-describedby={error ? errorId : undefined}
        wrapperProps={{ noMargin: true }}
      />
      {error && (
        <Text id={errorId} role="alert">
          {error}
        </Text>
      )}
      {emails.length > 0 && (
        <ul
          aria-label={t('invitees')}
          className={css({
            display: 'flex',
            flexWrap: 'wrap',
            gap: '0.375rem',
            padding: 0,
            margin: '0.5rem 0 0',
            listStyle: 'none',
          })}
        >
          {emails.map((email) => (
            <li
              key={email}
              className={css({
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.25rem',
                paddingLeft: '0.5rem',
                borderRadius: 'full',
                backgroundColor: 'muted',
                fontSize: '0.8125rem',
                overflowWrap: 'anywhere',
              })}
            >
              {email}
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label={t('removeInvitee', { email })}
                isDisabled={isDisabled}
                onPress={() => remove(email)}
              >
                <CloseIcon aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
