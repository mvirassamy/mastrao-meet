import { useTranslation } from 'react-i18next'
import { VideoIcon } from '@/icons'
import { LinkButton } from '@/primitives'
import { css } from '@/styled-system/css'
import {
  formatMeetingDuration,
  formatMeetingTimeRange,
} from '@/features/meetingHistory/utils/meetingHistoryFormat'

import type { PlannedMeeting } from '../../api/plannedMeetings'

/** Within this delay before its start, a planned meeting is the one to join. */
const STARTING_SOON_MS = 15 * 60_000

const isStartingSoon = (meeting: PlannedMeeting, now: Date) =>
  meeting.endsAt.getTime() > now.getTime() &&
  meeting.startsAt.getTime() - now.getTime() <= STARTING_SOON_MS

/**
 * Canonical planned times stay indicative; the host route checks current
 * rights. With `now` (today), the time left is shown and the join button
 * stands out in the quarter hour before the start.
 */
export const ScheduledMeetingEvent = ({
  meeting,
  color,
  locale,
  timeZone,
  now,
}: {
  meeting: PlannedMeeting
  color: string
  locale: string
  timeZone?: string
  now?: Date
}) => {
  const { t } = useTranslation(['home', 'meetingHistory'])
  const isJoinTime = now !== undefined && isStartingSoon(meeting, now)

  const status = () => {
    if (meeting.isClosed) return t('dashboard.meetings.closed')
    if (!now || meeting.endsAt <= now) return t('dashboard.meetings.planned')
    const duration = formatMeetingDuration(now, meeting.startsAt, locale)
    if (!duration) return t('dashboard.meetings.plannedNow')
    return t('dashboard.meetings.plannedIn', { duration })
  }

  return (
    <li
      data-meeting-id={meeting.id}
      style={{ borderLeftColor: color }}
      className={css({
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: '0.75rem',
        padding: '0.75rem 1rem 0.75rem 1.125rem',
        border: '1px solid token(colors.border)',
        borderLeftWidth: '4px',
        borderRadius: '12px',
        backgroundColor: 'card',
      })}
    >
      <div className={css({ minWidth: 0 })}>
        <p
          style={{ color }}
          className={css({ margin: 0, fontSize: '0.8125rem', fontWeight: 600 })}
        >
          {formatMeetingTimeRange(
            meeting.startsAt,
            meeting.endsAt,
            locale,
            timeZone
          )}
        </p>
        <h3
          className={css({
            margin: 0,
            fontSize: '0.9375rem',
            fontWeight: 500,
            overflowWrap: 'anywhere',
          })}
        >
          {meeting.title ?? t('untitled', { ns: 'meetingHistory' })}
        </h3>
        <p
          className={css({
            margin: 0,
            color: 'muted-foreground',
            fontSize: '0.8125rem',
          })}
        >
          {status()}
        </p>
      </div>
      {!meeting.isClosed && (
        <LinkButton
          href={`/host/${meeting.roomRef}`}
          variant={isJoinTime ? 'default' : 'outline'}
          size="sm"
        >
          {isJoinTime && <VideoIcon aria-hidden="true" />}
          {t('dashboard.meetings.join')}
        </LinkButton>
      )}
    </li>
  )
}
