import { useTranslation } from 'react-i18next'
import { Screen } from '@/layout/Screen'
import { Button, LinkButton } from '@/primitives'
import { RetryIcon } from '@/icons'
import { css } from '@/styled-system/css'

/**
 * Full-page error. Without a custom title it is the generic loading error and
 * offers a retry; with one (e.g. recording states) it only leads back home.
 */
export const ErrorScreen = ({
  title,
  body,
}: {
  title?: string
  body?: string
}) => {
  const { t } = useTranslation()
  const isLoadingError = !title

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
          src="/assets/illustrations/erreur.webp"
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
          {title ?? t('error.heading')}
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
          {body ?? t('error.body')}
        </p>
        <div
          className={css({
            display: 'flex',
            flexWrap: 'wrap',
            justifyContent: 'center',
            gap: '0.5rem',
            marginTop: '1.5rem',
          })}
        >
          <LinkButton href="/" variant={isLoadingError ? 'outline' : 'default'}>
            {t('backToHome')}
          </LinkButton>
          {isLoadingError && (
            <Button
              variant="default"
              icon={<RetryIcon aria-hidden="true" />}
              onPress={() => window.location.reload()}
            >
              {t('error.retry')}
            </Button>
          )}
        </div>
      </div>
    </Screen>
  )
}
