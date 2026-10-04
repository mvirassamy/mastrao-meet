import type { ReactNode, Ref } from 'react'
import { css, cva } from '@/styled-system/css'
import { STATUS_COLORS } from './meetingContent'

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
  tone?: 'neutral' | 'danger'
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

const STATE_DOT_COLORS = {
  neutral: STATUS_COLORS.absent,
  danger: STATUS_COLORS.failed,
} as const

const stateDot = css({
  width: '7px',
  height: '7px',
  borderRadius: '50%',
  flexShrink: 0,
})

const sectionState = cva({
  base: { minWidth: 0 },
  variants: {
    // Aligns the explanation and action with the title, after the dot.
    dotted: { true: { '& > :not(:first-child)': { paddingLeft: '15px' } } },
  },
})

/**
 * Compact state shown inside the Synthèse and Transcription sections: a
 * coloured dot and a title, with the explanation always visible below.
 * In-progress states have no dot: the section header shows a progress bar.
 */
export const MeetingSectionState = ({
  title,
  description,
  tone,
  action,
}: {
  title: string
  description: string
  tone?: 'neutral' | 'danger'
  action?: ReactNode
}) => (
  <div className={sectionState({ dotted: tone !== undefined })}>
    <p
      className={css({
        display: 'flex',
        alignItems: 'center',
        gap: '0.5rem',
        margin: 0,
        fontSize: '0.875rem',
        lineHeight: '1.25rem',
        fontWeight: 500,
      })}
    >
      {tone && (
        <span
          aria-hidden="true"
          className={stateDot}
          style={{ backgroundColor: STATE_DOT_COLORS[tone] }}
        />
      )}
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
)
