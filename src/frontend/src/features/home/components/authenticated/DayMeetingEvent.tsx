import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'wouter'
import { ChevronRightIcon } from '@/icons'
import { css } from '@/styled-system/css'
import type { MeetingHistoryItem } from '@/features/meetingHistory/api/types'
import { MeetingContentStatusIcon } from '@/features/meetingHistory/components/MeetingContentStatusIcon'
import { MeetingParticipantsStack } from '@/features/meetingHistory/components/MeetingParticipantsStack'
import { RowOverlayCell } from '@/features/meetingHistory/components/RowOverlayCell'
import { meetingHistoryDetailPath } from '@/features/meetingHistory/paths'
import { rememberOpenedMeeting } from '@/features/meetingHistory/utils/focusReturn'
import {
  formatMeetingDuration,
  formatMeetingTimeRange,
} from '@/features/meetingHistory/utils/meetingHistoryFormat'

/** A past meeting shown as a calendar event in the home day view. */
export const DayMeetingEvent = ({
  item,
  color,
  locale,
  timeZone,
}: {
  item: MeetingHistoryItem
  color: string
  locale: string
  timeZone?: string
}) => {
  const { t } = useTranslation('meetingHistory')
  const linkRef = useRef<HTMLAnchorElement>(null)
  const openMeeting = () => linkRef.current?.click()
  const time = [
    formatMeetingTimeRange(item.startedAt, item.endedAt, locale, timeZone),
    formatMeetingDuration(item.startedAt, item.endedAt, locale),
  ]
    .filter(Boolean)
    .join(' · ')
  const participantCount = Math.max(
    item.participantCount ?? 0,
    item.participantNames.length
  )

  return (
    <li
      style={{ borderLeftColor: color }}
      className={css({
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        gap: '1rem',
        padding: '0.75rem 1rem 0.75rem 1.125rem',
        border: '1px solid token(colors.border)',
        borderLeftWidth: '4px',
        borderRadius: '12px',
        backgroundColor: 'card',
        transition: 'background 150ms',
        _hover: {
          backgroundColor: 'var(--workspace-paper, token(colors.muted))',
        },
      })}
    >
      <div className={css({ flex: 1, minWidth: 0 })}>
        <p
          style={{ color }}
          className={css({
            margin: 0,
            fontSize: '0.8125rem',
            lineHeight: '1.25rem',
            fontWeight: 600,
          })}
        >
          {time}
        </p>
        <Link
          ref={linkRef}
          to={meetingHistoryDetailPath(item.id)}
          data-meeting-id={item.id}
          onClick={() => rememberOpenedMeeting(item.id)}
          className={css({
            display: 'block',
            marginTop: '0.125rem',
            overflow: 'hidden',
            color: 'foreground',
            fontSize: '0.9375rem',
            lineHeight: '1.375rem',
            fontWeight: 500,
            textDecoration: 'none',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            outline: 'none',
            _after: { content: '""', position: 'absolute', inset: 0 },
            '&:focus-visible::after': {
              outline: '2px solid token(colors.ring)',
              outlineOffset: '-2px',
              borderRadius: '8px',
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
        {participantCount > 0 && (
          <div className={css({ display: 'flex', marginTop: '0.5rem' })}>
            <RowOverlayCell onOpen={openMeeting}>
              <MeetingParticipantsStack
                count={item.participantCount}
                names={item.participantNames}
              />
              <span
                aria-hidden="true"
                className={css({
                  color: 'muted-foreground',
                  fontSize: '0.8125rem',
                  lineHeight: '1.25rem',
                })}
              >
                {t('participants', { count: participantCount })}
              </span>
            </RowOverlayCell>
          </div>
        )}
      </div>
      <RowOverlayCell onOpen={openMeeting}>
        <MeetingContentStatusIcon kind="summary" status={item.summaryStatus} />
        <MeetingContentStatusIcon
          kind="transcript"
          status={item.transcriptStatus}
        />
      </RowOverlayCell>
      <ChevronRightIcon
        size={18}
        aria-hidden="true"
        className={css({ flexShrink: 0, color: 'muted-foreground' })}
      />
    </li>
  )
}
