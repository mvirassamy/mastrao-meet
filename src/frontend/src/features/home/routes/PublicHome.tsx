import { useTranslation } from 'react-i18next'
import { DialogTrigger } from 'react-aria-components'
import { useEffect, useState, type ReactNode } from 'react'
import { Button } from '@/primitives'
import { Screen } from '@/layout/Screen'
import { layoutStore } from '@/stores/layout'
import { css, cx } from '@/styled-system/css'
import { LoginButton } from '@/components/LoginButton'
import { LoadingScreen } from '@/components/LoadingScreen'
import { useConfig } from '@/api/useConfig'
import { reportError } from '@/features/analytics/telemetry'
import {
  AdminIcon,
  EffectsIcon,
  LinkIcon,
  VideoIcon,
  type AppIconComponent,
} from '@/icons'
import { JoinMeetingDialog } from '../components/JoinMeetingDialog'

/*
 * Public home: Google Meet structure, Mastrao Platform look, three arguments.
 */

const page = css({
  width: '100%',
  backgroundColor: 'background',
  color: 'foreground',
})

const container = css({
  width: '100%',
  maxWidth: '72rem',
  marginX: 'auto',
  paddingX: { base: '1.25rem', md: '2rem' },
})

const JoinButtons = ({ centered = false }: { centered?: boolean }) => {
  const { t } = useTranslation('home')
  return (
    <div
      className={css({
        display: 'flex',
        flexWrap: 'wrap',
        gap: '0.75rem',
        justifyContent: centered ? 'center' : { base: 'center', lg: 'start' },
      })}
    >
      <LoginButton proConnectHint={false} />
      <DialogTrigger>
        <Button variant="outline">{t('joinMeeting')}</Button>
        <JoinMeetingDialog />
      </DialogTrigger>
    </div>
  )
}

const Feature = ({
  icon: Icon,
  eyebrow,
  title,
  body,
  image,
  reverse,
}: {
  icon: AppIconComponent
  eyebrow: string
  title: string
  body: string
  image: string
  reverse: boolean
}) => (
  <section
    className={css({
      display: 'grid',
      gridTemplateColumns: { base: '1fr', lg: '1fr 1fr' },
      alignItems: 'center',
      gap: { base: '2rem', lg: '4.5rem' },
      paddingY: { base: '2.5rem', lg: '4rem' },
    })}
  >
    <div
      className={css({
        position: 'relative',
        display: 'grid',
        placeItems: 'center',
        aspectRatio: '4 / 3',
        width: '100%',
        maxWidth: '30rem',
        marginX: 'auto',
        order: { base: 0, lg: reverse ? 2 : 0 },
      })}
    >
      <div
        aria-hidden="true"
        className={css({
          position: 'absolute',
          inset: '4%',
          borderRadius: 'full',
          background:
            'radial-gradient(closest-side, rgb(45 91 227 / 0.10), transparent)',
        })}
      />
      <img
        src={image}
        alt=""
        loading="lazy"
        decoding="async"
        className={css({
          position: 'relative',
          width: '100%',
          height: '100%',
          objectFit: 'contain',
          userSelect: 'none',
          pointerEvents: 'none',
        })}
      />
    </div>
    <div
      className={css({
        order: 1,
        maxWidth: '30rem',
        marginX: { base: 'auto', lg: 0 },
        textAlign: { base: 'center', lg: 'left' },
      })}
    >
      <p
        className={css({
          display: 'inline-flex',
          alignItems: 'center',
          gap: '0.5rem',
          margin: 0,
          fontSize: '0.8125rem',
          fontWeight: 500,
          color: 'primary',
        })}
      >
        <span
          aria-hidden="true"
          className={css({
            display: 'grid',
            placeItems: 'center',
            width: '28px',
            height: '28px',
            borderRadius: '8px',
            backgroundColor: 'accent',
          })}
        >
          <Icon size={16} />
        </span>
        {eyebrow}
      </p>
      <h2
        className={css({
          marginTop: '1rem',
          marginBottom: 0,
          fontSize: { base: '1.625rem', md: '2rem' },
          lineHeight: 1.2,
          fontWeight: 600,
          letterSpacing: '-0.02em',
          textWrap: 'balance',
        })}
      >
        {title}
      </h2>
      <p
        className={css({
          marginTop: '0.875rem',
          marginBottom: 0,
          fontSize: '1rem',
          lineHeight: 1.65,
          textWrap: 'pretty',
        })}
      >
        {body}
      </p>
    </div>
  </section>
)

const Hero = ({ children }: { children: ReactNode }) => (
  <section
    className={css({
      display: 'grid',
      gridTemplateColumns: { base: '1fr', lg: '1.05fr 1fr' },
      alignItems: 'center',
      gap: { base: '2.5rem', lg: '4rem' },
      paddingTop: { base: '2.5rem', lg: '5rem' },
      paddingBottom: { base: '3rem', lg: '5.5rem' },
    })}
  >
    {children}
  </section>
)

export const PublicHome = () => {
  const { t } = useTranslation('home')
  const [redirectFailed, setRedirectFailed] = useState(false)
  const { data } = useConfig()

  // Deployments may point the public home to an external site.
  useEffect(() => {
    const checkSiteAndRedirect = async () => {
      if (!data?.external_home_url) return
      try {
        await fetch(data.external_home_url, {
          method: 'HEAD', // Use HEAD to avoid downloading the full page
          mode: 'no-cors', // Needed for cross-origin requests
        })
        window.location.replace(data.external_home_url)
      } catch (error) {
        setRedirectFailed(true)
        reportError('generic_failure', error, {
          context: 'Site is not reachable:',
        })
      }
    }

    checkSiteAndRedirect()
  }, [data])

  // Sticky translucent header, like the Platform vitrine, on this page only.
  useEffect(() => {
    layoutStore.headerAppearance = 'glass'
    return () => {
      layoutStore.headerAppearance = 'default'
    }
  }, [])

  if (data?.external_home_url && !redirectFailed) {
    return <LoadingScreen header={false} footer={false} delay={0} />
  }

  const features = [
    {
      key: 'slide1',
      icon: AdminIcon,
      image: '/assets/illustrations/argument-france.webp',
    },
    {
      key: 'slide2',
      icon: LinkIcon,
      image: '/assets/illustrations/argument-lien.webp',
    },
    {
      key: 'slide3',
      icon: EffectsIcon,
      image: '/assets/illustrations/argument-ia.webp',
    },
  ] as const

  return (
    <Screen>
      <div className={cx('authenticated-meet-workspace', page)}>
        <div className={container}>
          <Hero>
            <div
              className={css({
                textAlign: { base: 'center', lg: 'left' },
                maxWidth: { base: '36rem', lg: 'none' },
                marginX: { base: 'auto', lg: 0 },
              })}
            >
              <p
                className={css({
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '0.625rem',
                  margin: 0,
                  fontSize: '1.125rem',
                  fontWeight: 500,
                  color: 'var(--heading-foreground)',
                })}
              >
                <span
                  aria-hidden="true"
                  className={css({
                    display: 'grid',
                    placeItems: 'center',
                    width: '32px',
                    height: '32px',
                    borderRadius: '9px',
                    backgroundColor: 'primary',
                    color: 'primary-foreground',
                  })}
                >
                  <VideoIcon size={18} />
                </span>
                Mastrao Visio
              </p>
              <h1
                className={css({
                  marginTop: '1.25rem',
                  marginBottom: 0,
                  fontSize: { base: '2.25rem', md: '2.875rem', lg: '3.125rem' },
                  lineHeight: 1.08,
                  fontWeight: 600,
                  letterSpacing: '-0.035em',
                  textWrap: 'balance',
                })}
              >
                {t('heading')}
              </h1>
              <p
                className={css({
                  marginTop: '1.25rem',
                  marginBottom: '2rem',
                  fontSize: { base: '1.0625rem', md: '1.1875rem' },
                  lineHeight: 1.55,
                  textWrap: 'balance',
                  maxWidth: '32rem',
                  marginX: { base: 'auto', lg: 0 },
                })}
              >
                {t('intro')}
              </p>
              <JoinButtons />
            </div>
            <img
              src="/assets/illustrations/accueil-hero.webp"
              alt=""
              width={1000}
              height={645}
              decoding="async"
              fetchPriority="high"
              className={css({
                display: 'block',
                width: '100%',
                maxWidth: '36rem',
                height: 'auto',
                marginX: 'auto',
                userSelect: 'none',
                pointerEvents: 'none',
              })}
            />
          </Hero>

          <div
            className={css({
              borderTop: '1px solid token(colors.border)',
            })}
          >
            {features.map(({ key, icon, image }, index) => (
              <Feature
                key={key}
                icon={icon}
                image={image}
                eyebrow={t(`homeV2.eyebrows.${key}`)}
                title={t(`introSlider.${key}.title`)}
                body={t(`introSlider.${key}.body`)}
                reverse={index % 2 === 1}
              />
            ))}
          </div>

          <section
            className={css({
              marginTop: { base: '1rem', lg: '2rem' },
              marginBottom: { base: '3rem', lg: '5rem' },
              paddingX: { base: '1.5rem', md: '3rem' },
              paddingY: { base: '2.5rem', md: '3.5rem' },
              borderRadius: '24px',
              backgroundColor: 'accent',
              textAlign: 'center',
            })}
          >
            <h2
              className={css({
                margin: 0,
                fontSize: { base: '1.5rem', md: '1.875rem' },
                lineHeight: 1.2,
                fontWeight: 600,
                letterSpacing: '-0.02em',
                textWrap: 'balance',
              })}
            >
              {t('homeV2.cta.title')}
            </h2>
            <p
              className={css({
                marginTop: '0.75rem',
                marginBottom: '1.75rem',
                fontSize: '1rem',
                lineHeight: 1.6,
              })}
            >
              {t('homeV2.cta.body')}
            </p>
            <JoinButtons centered />
          </section>
        </div>
      </div>
    </Screen>
  )
}

export default PublicHome
