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

/** What a content tab shows instead of its content. */
export type SectionStateLook = 'pending' | 'failed' | 'empty'

const stateIcon = cva({
  base: {
    width: '56px',
    height: '56px',
    userSelect: 'none',
  },
  variants: {
    look: {
      pending: {},
      // Greyed: nothing to read in this tab.
      failed: { filter: 'grayscale(1)', opacity: 0.5 },
      empty: { filter: 'grayscale(1)', opacity: 0.5 },
    },
  },
})

/** Ring turning around the icon while the content is prepared. */
const PendingRing = () => (
  <span
    aria-hidden="true"
    className={css({
      position: 'absolute',
      inset: '-4px',
      borderRadius: 'full',
      border: '2px solid #dfe6fa',
      borderTopColor: 'primary',
      animation: 'rotate 1.1s linear infinite',
      _motionReduce: { animation: 'none' },
    })}
  />
)

/** Red mark on the greyed icon when the processing failed. */
const FailedBadge = () => (
  <span
    aria-hidden="true"
    className={css({
      position: 'absolute',
      right: '-2px',
      bottom: '0',
      display: 'grid',
      placeItems: 'center',
      width: '22px',
      height: '22px',
      borderRadius: 'full',
      border: '2px solid token(colors.card)',
      backgroundColor: STATUS_COLORS.failed,
      color: 'white',
      fontSize: '0.75rem',
      fontWeight: 700,
      lineHeight: 1,
    })}
  >
    !
  </span>
)

/**
 * State shown inside a content tab: the tab's 3D icon in the middle (a
 * turning ring while it is prepared, greyed with a red mark on failure,
 * greyed when there is nothing), then a title, an explanation and, when
 * possible, an action.
 */
export const MeetingSectionState = ({
  icon,
  look,
  title,
  description,
  action,
}: {
  /** The tab's 3D icon (public asset path). */
  icon: string
  look: SectionStateLook
  title: string
  description: string
  action?: ReactNode
}) => (
  <div
    className={css({
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      maxWidth: '24rem',
      marginX: 'auto',
      paddingY: { base: '2rem', md: '3rem' },
      textAlign: 'center',
    })}
  >
    <span
      className={css({
        position: 'relative',
        display: 'grid',
        placeItems: 'center',
        width: '64px',
        height: '64px',
        marginBottom: '0.75rem',
      })}
    >
      <img
        src={icon}
        alt=""
        aria-hidden="true"
        width={56}
        height={56}
        className={stateIcon({ look })}
      />
      {look === 'pending' && <PendingRing />}
      {look === 'failed' && <FailedBadge />}
    </span>
    <p
      className={css({
        margin: 0,
        color: 'var(--heading-foreground)',
        fontSize: '0.9375rem',
        lineHeight: '1.5rem',
        fontWeight: 600,
      })}
    >
      {title}
    </p>
    <p
      className={css({
        marginTop: '0.25rem',
        marginBottom: 0,
        color: 'muted-foreground',
        fontSize: '0.8125rem',
        lineHeight: '1.25rem',
        textWrap: 'balance',
      })}
    >
      {description}
    </p>
    {action && <div className={css({ marginTop: '1rem' })}>{action}</div>}
  </div>
)
