import { useTranslation } from 'react-i18next'
import { DialogTrigger } from 'react-aria-components'
import { Screen } from '@/layout/Screen'
import { Button, LinkButton } from '@/primitives'
import { css } from '@/styled-system/css'
import { JoinMeetingDialog } from '@/features/home/components/JoinMeetingDialog'

/** Unknown route: usually a mistyped meeting link. */
export const NotFoundScreen = () => {
  // Preload "home" so opening the join dialog never suspends the page.
  const { t } = useTranslation(['global', 'home'])
  // Same format as the join dialog accepts (origin + code).
  const example = `${window.location.origin}/abc-defg-hij`

  return (
    <Screen layout="centered">
      <div
        className={css({
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          width: '100%',
          maxWidth: '36rem',
          marginX: 'auto',
          paddingX: '1.25rem',
          paddingY: { base: '2rem', md: '3rem' },
          textAlign: 'center',
        })}
      >
        <img
          src="/assets/illustrations/code-reunion-erreur.webp"
          alt=""
          width={768}
          height={512}
          decoding="async"
          className={css({
            display: 'block',
            width: { base: '220px', md: '300px' },
            height: 'auto',
            marginBottom: '0.75rem',
            userSelect: 'none',
            pointerEvents: 'none',
          })}
        />
        <h1
          className={css({
            margin: 0,
            fontSize: { base: '1.5rem', md: '1.75rem' },
            lineHeight: 1.25,
            fontWeight: 600,
            letterSpacing: '-0.025em',
            textWrap: 'balance',
          })}
        >
          {t('notFound.heading')}
        </h1>
        <p
          className={css({
            marginTop: '0.625rem',
            marginBottom: 0,
            color: 'muted-foreground',
            fontSize: '0.9375rem',
            lineHeight: '1.5rem',
            textWrap: 'balance',
          })}
        >
          {t('notFound.body')}
        </p>
        <code
          className={css({
            display: 'inline-block',
            maxWidth: '100%',
            marginTop: '0.75rem',
            paddingX: '0.625rem',
            paddingY: '0.25rem',
            borderRadius: '8px',
            border: '1px solid token(colors.border)',
            backgroundColor: 'card',
            color: 'var(--heading-foreground)',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            fontSize: '0.8125rem',
            overflowWrap: 'anywhere',
          })}
        >
          {example}
        </code>
        <div
          className={css({
            display: 'flex',
            flexWrap: 'wrap',
            justifyContent: 'center',
            gap: '0.5rem',
            marginTop: '1.5rem',
          })}
        >
          <LinkButton href="/" variant="outline">
            {t('backToHome')}
          </LinkButton>
          <DialogTrigger>
            <Button variant="default">{t('notFound.enterCode')}</Button>
            <JoinMeetingDialog />
          </DialogTrigger>
        </div>
      </div>
    </Screen>
  )
}
