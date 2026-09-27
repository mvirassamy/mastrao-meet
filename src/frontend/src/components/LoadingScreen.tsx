import { Screen, type ScreenProps } from '@/layout/Screen'
import { DelayedRender } from './DelayedRender'
import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'

const RING_SIZE = 64

/**
 * Full-page loading state: the Mastrao mark inside a thin rotating ring and a
 * discreet status label. Motion stops when the user prefers reduced motion.
 */
export const LoadingScreen = ({
  delay = 500,
  header = undefined,
  footer = undefined,
  layout = 'centered',
}: {
  delay?: number
} & Omit<ScreenProps, 'children'>) => {
  const { t } = useTranslation()

  return (
    <DelayedRender delay={delay}>
      <Screen layout={layout} header={header} footer={footer}>
        <div
          role="status"
          aria-live="polite"
          className={css({
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '1rem',
            width: '100%',
            minHeight: { base: '50vh', md: '55vh' },
            paddingX: '1.25rem',
            textAlign: 'center',
          })}
        >
          <div
            aria-hidden="true"
            className={css({
              position: 'relative',
              display: 'grid',
              placeItems: 'center',
              borderRadius: 'full',
              backgroundColor: 'card',
              boxShadow: '0 1px 3px rgb(20 33 58 / 0.08)',
            })}
            style={{ width: RING_SIZE, height: RING_SIZE }}
          >
            <span
              className={css({
                position: 'absolute',
                inset: 0,
                borderRadius: 'full',
                border: '2.5px solid token(colors.border)',
                borderTopColor: 'primary',
                animation: 'rotate 0.9s linear infinite',
                '@media (prefers-reduced-motion: reduce)': {
                  animation: 'none',
                },
              })}
            />
            <img
              src="/assets/mastrao-logo-icon.png"
              alt=""
              width={28}
              height={28}
              decoding="async"
              className={css({
                display: 'block',
                width: '28px',
                height: '28px',
                objectFit: 'contain',
                userSelect: 'none',
              })}
            />
          </div>
          <p
            className={css({
              margin: 0,
              color: 'muted-foreground',
              fontSize: '0.9375rem',
              lineHeight: '1.5rem',
            })}
          >
            {t('loading')}
          </p>
        </div>
      </Screen>
    </DelayedRender>
  )
}
