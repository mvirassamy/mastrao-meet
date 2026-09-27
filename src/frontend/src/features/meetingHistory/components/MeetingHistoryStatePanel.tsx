import type { ReactNode, Ref } from 'react'
import { css, cva } from '@/styled-system/css'

const iconWrapper = cva({
  base: {
    display: 'grid',
    placeItems: 'center',
    width: '44px',
    height: '44px',
    borderRadius: '12px',
    flexShrink: 0,
  },
  variants: {
    tone: {
      neutral: { backgroundColor: 'muted', color: 'muted-foreground' },
      info: { backgroundColor: 'info', color: 'info-foreground' },
      danger: { backgroundColor: 'recording', color: 'recording-foreground' },
    },
  },
  defaultVariants: { tone: 'neutral' },
})

type MeetingHistoryStatePanelProps = {
  title: string
  description: string
  icon?: ReactNode
  /** Decorative illustration shown instead of the icon (public asset path). */
  illustration?: string
  tone?: 'neutral' | 'info' | 'danger'
  action?: ReactNode
  headingLevel?: 1 | 2
  headingRef?: Ref<HTMLHeadingElement>
  role?: 'status' | 'alert'
}

/** Centered page-level state: empty history, errors, missing meeting. */
export const MeetingHistoryStatePanel = ({
  title,
  description,
  icon,
  illustration,
  tone = 'neutral',
  action,
  headingLevel = 2,
  headingRef,
  role,
}: MeetingHistoryStatePanelProps) => {
  const Heading = headingLevel === 1 ? 'h1' : 'h2'

  return (
    <div
      role={role}
      className={css({
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        maxWidth: '32rem',
        marginX: 'auto',
        paddingY: { base: '2rem', md: '3rem' },
        textAlign: 'center',
      })}
    >
      {illustration ? (
        <img
          src={illustration}
          alt=""
          width={768}
          height={512}
          decoding="async"
          className={css({
            display: 'block',
            width: { base: '224px', md: '310px' },
            height: 'auto',
            marginBottom: '0.75rem',
            userSelect: 'none',
            pointerEvents: 'none',
          })}
        />
      ) : (
        icon && (
          <span
            aria-hidden="true"
            className={`${iconWrapper({ tone })} ${css({ marginBottom: '1rem' })}`}
          >
            {icon}
          </span>
        )
      )}
      <Heading
        ref={headingRef}
        tabIndex={headingRef ? -1 : undefined}
        className={css({
          margin: 0,
          fontSize: { base: '1.25rem', md: '1.375rem' },
          lineHeight: 1.3,
          fontWeight: 600,
          letterSpacing: '-0.02em',
          textWrap: 'balance',
        })}
      >
        {title}
      </Heading>
      <p
        className={css({
          marginTop: '0.5rem',
          marginBottom: action ? '1.25rem' : 0,
          color: 'muted-foreground',
          fontSize: '0.875rem',
          lineHeight: '1.25rem',
          textWrap: 'balance',
        })}
      >
        {description}
      </p>
      {action}
    </div>
  )
}

/** Compact state shown inside the Synthèse and Transcription sections. */
export const MeetingSectionState = ({
  title,
  description,
  icon,
  tone = 'neutral',
  action,
}: {
  title: string
  description: string
  icon: ReactNode
  tone?: 'neutral' | 'info' | 'danger'
  action?: ReactNode
}) => (
  <div
    className={css({
      display: 'flex',
      alignItems: 'flex-start',
      gap: '0.75rem',
    })}
  >
    <span
      aria-hidden="true"
      className={`${iconWrapper({ tone })} ${css({
        width: '36px',
        height: '36px',
        borderRadius: '10px',
      })}`}
    >
      {icon}
    </span>
    <div className={css({ minWidth: 0 })}>
      <p
        className={css({
          margin: 0,
          fontSize: '0.875rem',
          lineHeight: '1.25rem',
          fontWeight: 600,
        })}
      >
        {title}
      </p>
      <p
        className={css({
          marginTop: '0.125rem',
          marginBottom: 0,
          color: 'muted-foreground',
          fontSize: '0.8125rem',
          lineHeight: '1.25rem',
        })}
      >
        {description}
      </p>
      {action && <div className={css({ marginTop: '0.75rem' })}>{action}</div>}
    </div>
  </div>
)
