import { useTranslation } from 'react-i18next'
import type { ReactNode } from 'react'
import { css } from '@/styled-system/css'
import { LoginButton } from '@/components/LoginButton'
import { MeetingJoinField } from './authenticated/MeetingJoinField'
import { MoreLink } from './MoreLink'

/*
 * Closing section of the public home: create a meeting with an account,
 * or join one directly with its code or link.
 */
export const PublicHomeCta = () => {
  const { t } = useTranslation('home', { keyPrefix: 'homeV2.cta' })

  return (
    <section
      className={css({
        marginTop: { base: '1rem', lg: '2rem' },
        marginBottom: { base: '3rem', lg: '5rem' },
      })}
    >
      <div className={css({ textAlign: 'center', marginBottom: '2rem' })}>
        <h2
          className={css({
            margin: 0,
            fontSize: { base: '1.625rem', md: '2rem' },
            lineHeight: 1.2,
            fontWeight: 600,
            letterSpacing: '-0.02em',
            textWrap: 'balance',
          })}
        >
          {t('title')}
        </h2>
        <p
          className={css({
            marginTop: '0.75rem',
            marginBottom: 0,
            fontSize: '1rem',
            lineHeight: 1.6,
          })}
        >
          {t('body')}
        </p>
      </div>
      <div
        className={css({
          display: 'grid',
          gridTemplateColumns: { base: '1fr', md: '1fr 1fr' },
          gap: '1.25rem',
        })}
      >
        <StartPath
          icon="/assets/home/reunion-3d.webp"
          title={t('create.title')}
          body={t('create.body')}
        >
          <LoginButton proConnectHint={false} />
        </StartPath>
        <StartPath
          icon="/assets/home/calendrier-3d.webp"
          title={t('join.title')}
          body={t('join.body')}
        >
          <MeetingJoinField />
        </StartPath>
      </div>
      <div className={css({ textAlign: 'center' })}>
        <MoreLink />
      </div>
    </section>
  )
}

const StartPath = ({
  icon,
  title,
  body,
  children,
}: {
  icon: string
  title: string
  body: string
  children: ReactNode
}) => (
  <div
    className={css({
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
      border: '1px solid token(colors.border)',
      borderRadius: '16px',
      backgroundColor: 'card',
    })}
  >
    <div
      aria-hidden="true"
      className={css({
        display: 'grid',
        placeItems: 'center',
        height: '7.5rem',
        background:
          'token(colors.accent) url(/assets/home/aquarelle.webp) center / cover',
      })}
    >
      <img src={icon} alt="" width={72} height={72} loading="lazy" />
    </div>
    <div
      className={css({
        display: 'flex',
        flexDirection: 'column',
        flex: 1,
        alignItems: 'flex-start',
        padding: { base: '1.25rem', md: '1.5rem' },
      })}
    >
      <h3
        className={css({
          margin: 0,
          fontSize: '1.1875rem',
          lineHeight: 1.3,
          fontWeight: 600,
        })}
      >
        {title}
      </h3>
      <p
        className={css({
          marginTop: '0.375rem',
          marginBottom: '1.25rem',
          fontSize: '0.9375rem',
          lineHeight: 1.55,
          color: 'muted-foreground',
        })}
      >
        {body}
      </p>
      <div className={css({ marginTop: 'auto', width: '100%' })}>
        {children}
      </div>
    </div>
  </div>
)
