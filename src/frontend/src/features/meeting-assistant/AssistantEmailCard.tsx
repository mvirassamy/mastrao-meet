import { useTranslation } from 'react-i18next'
import { CheckIcon, CopyIcon, MailIcon } from '@/icons'
import { Button, LinkButton } from '@/primitives'
import { useClipboardCopy } from '@/features/meetingHistory/components/useClipboardCopy'
import { css } from '@/styled-system/css'

const EMAIL_ICON = '/assets/illustrations/email.webp'

/** A follow-up e-mail drafted by the assistant, ready to copy or send. */
export function AssistantEmailCard({
  subject,
  body,
}: {
  subject: string
  body: string
}) {
  const { t } = useTranslation('meetingAssistant')
  const { copy, copyText } = useClipboardCopy()
  const mailto = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
  return (
    <section
      aria-label={t('email.label')}
      className={css({
        border: '1px solid token(colors.border)',
        borderRadius: '12px',
        backgroundColor: 'card',
        overflow: 'hidden',
      })}
    >
      <header
        className={css({
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          padding: '0.625rem 0.75rem',
          borderBottom: '1px solid token(colors.border)',
        })}
      >
        <img
          src={EMAIL_ICON}
          alt=""
          aria-hidden="true"
          width={20}
          height={20}
        />
        <h3
          className={css({
            margin: 0,
            fontSize: '0.875rem',
            fontWeight: 600,
            color: 'var(--heading-foreground)',
          })}
        >
          {subject}
        </h3>
      </header>
      <p
        className={css({
          margin: 0,
          padding: '0.75rem',
          fontSize: '0.8125rem',
          lineHeight: '1.6',
          whiteSpace: 'pre-wrap',
          overflowWrap: 'anywhere',
        })}
      >
        {body}
      </p>
      <footer
        className={css({
          display: 'flex',
          flexWrap: 'wrap',
          gap: '0.375rem',
          padding: '0.375rem 0.5rem',
          borderTop: '1px solid token(colors.border)',
        })}
      >
        <Button
          variant="ghost"
          size="xs"
          icon={
            copy === 'copied' ? (
              <CheckIcon aria-hidden="true" />
            ) : (
              <CopyIcon aria-hidden="true" />
            )
          }
          onPress={() => void copyText(`${subject}\n\n${body}`)}
        >
          {t(`email.${copy === 'idle' ? 'copy' : copy}`)}
        </Button>
        <LinkButton variant="ghost" size="xs" href={mailto}>
          <MailIcon aria-hidden="true" />
          {t('email.open')}
        </LinkButton>
        <span role="status" className={css({ srOnly: true })}>
          {copy !== 'idle' && t(`email.${copy}`)}
        </span>
      </footer>
    </section>
  )
}
