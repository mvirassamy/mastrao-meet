import { type ReactNode, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'wouter'
import { ChevronRightIcon } from '@/icons'
import { css } from '@/styled-system/css'
import type { MeetingHistoryItem } from '../api/types'
import { meetingHistoryDetailPath } from '../paths'
import { rememberOpenedMeeting } from '../utils/focusReturn'
import {
  formatMeetingDuration,
  formatMeetingTimeRange,
} from '../utils/meetingHistoryFormat'
import { MeetingContentStatusIcon } from './MeetingContentStatusIcon'
import { MeetingParticipantsStack } from './MeetingParticipantsStack'

/**
 * Meetings of one day as a card of rows: title, time, participants, summary
 * and transcript status. Shared by the history list and the home calendar.
 */
export const MeetingHistoryRows = ({
  items,
  locale,
  timeZone,
}: {
  items: MeetingHistoryItem[]
  locale: string
  timeZone?: string
}) => (
  <ul
    className={css({
      margin: 0,
      padding: 0,
      listStyle: 'none',
      border: '1px solid token(colors.border)',
      borderRadius: '12px',
      backgroundColor: 'card',
      overflow: 'hidden',
    })}
  >
    {items.map((item) => (
      <MeetingHistoryRow
        key={item.id}
        item={item}
        locale={locale}
        timeZone={timeZone}
      />
    ))}
  </ul>
)

/**
 * Row cell drawn above the row link overlay so its tooltips can show. It only
 * forwards a mouse click to the row link; keyboard and screen reader users
 * reach the same link directly.
 */
const RowOverlayCell = ({
  onOpen,
  children,
}: {
  onOpen: () => void
  children: ReactNode
}) => (
  // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
  <div
    onClick={onOpen}
    className={css({
      position: 'relative',
      zIndex: 1,
      display: 'flex',
      gap: '0.625rem',
      cursor: 'pointer',
    })}
  >
    {children}
  </div>
)

const MeetingHistoryRow = ({
  item,
  locale,
  timeZone,
}: {
  item: MeetingHistoryItem
  locale: string
  timeZone?: string
}) => {
  const { t } = useTranslation('meetingHistory')
  const linkRef = useRef<HTMLAnchorElement>(null)
  const duration = formatMeetingDuration(item.startedAt, item.endedAt, locale)
  const meta = [
    formatMeetingTimeRange(item.startedAt, item.endedAt, locale, timeZone),
    duration,
  ].filter(Boolean)

  return (
    <li
      className={css({
        position: 'relative',
        display: 'grid',
        gridTemplateColumns: {
          base: 'minmax(0, 1fr) auto auto auto',
          // The participants start in the middle of the row on wide screens.
          md: 'minmax(0, 1fr) minmax(5.5rem, 1fr) auto auto',
        },
        alignItems: 'center',
        columnGap: '0.75rem',
        rowGap: '0.5rem',
        padding: {
          base: '0.875rem 0.75rem 0.875rem 1rem',
          md: '0.875rem 1rem',
        },
        transition: 'background 150ms',
        '&:not(:last-child)': {
          borderBottom: '1px solid token(colors.border)',
        },
        _hover: {
          backgroundColor: 'var(--workspace-paper, token(colors.muted))',
        },
      })}
    >
      <div className={css({ minWidth: 0 })}>
        <Link
          ref={linkRef}
          to={meetingHistoryDetailPath(item.id)}
          data-meeting-id={item.id}
          onClick={() => rememberOpenedMeeting(item.id)}
          className={css({
            display: 'block',
            overflow: 'hidden',
            color: 'foreground',
            fontSize: '0.9375rem',
            lineHeight: '1.375rem',
            fontWeight: 500,
            textDecoration: 'none',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            outline: 'none',
            _after: {
              content: '""',
              position: 'absolute',
              inset: 0,
            },
            '&:focus-visible::after': {
              outline: '2px solid token(colors.ring)',
              outlineOffset: '-2px',
              borderRadius: '11px',
            },
          })}
        >
          {item.title ?? t('untitled')}
          {/* The status icons are decorative: their meaning is read here. */}
          <span className={css({ srOnly: true })}>
            {t(`status.summary.${item.summaryStatus}`)}
          </span>
          <span className={css({ srOnly: true })}>
            {t(`status.transcript.${item.transcriptStatus}`)}
          </span>
        </Link>
        <p
          className={css({
            marginTop: '0.125rem',
            marginBottom: 0,
            color: 'muted-foreground',
            fontSize: '0.8125rem',
            lineHeight: '1.25rem',
          })}
        >
          {meta.join(' · ')}
        </p>
      </div>
      <RowOverlayCell onOpen={() => linkRef.current?.click()}>
        <MeetingParticipantsStack
          count={item.participantCount}
          names={item.participantNames}
        />
      </RowOverlayCell>
      <RowOverlayCell onOpen={() => linkRef.current?.click()}>
        <MeetingContentStatusIcon kind="summary" status={item.summaryStatus} />
        <MeetingContentStatusIcon
          kind="transcript"
          status={item.transcriptStatus}
        />
      </RowOverlayCell>
      <ChevronRightIcon
        size={18}
        aria-hidden="true"
        className={css({
          color: 'muted-foreground',
        })}
      />
    </li>
  )
}
