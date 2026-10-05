import { useTranslation } from 'react-i18next'
import { LinkButton } from '@/primitives'
import { css } from '@/styled-system/css'
import { formatMeetingTimeRange } from '@/features/meetingHistory/utils/meetingHistoryFormat'

import type { PlannedMeeting } from '../../api/plannedMeetings'

/** Canonical planned times stay indicative; the host route checks current rights. */
export const ScheduledMeetingEvent = ({
  meeting,
  color,
  locale,
  timeZone,
}: {
  meeting: PlannedMeeting
  color: string
  locale: string
  timeZone?: string
}) => {
  const { t } = useTranslation(['home', 'meetingHistory'])
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
        <h2
          className={css({
            margin: 0,
            fontSize: '0.9375rem',
            fontWeight: 500,
            overflowWrap: 'anywhere',
          })}
        >
          {meeting.title ?? t('untitled', { ns: 'meetingHistory' })}
        </h2>
        <p
          className={css({
            margin: 0,
            color: 'muted-foreground',
            fontSize: '0.8125rem',
          })}
        >
          {t(
            meeting.isClosed
              ? 'dashboard.meetings.closed'
              : 'dashboard.meetings.planned'
          )}
        </p>
      </div>
      {!meeting.isClosed && (
        <LinkButton
          href={`/host/${meeting.roomRef}`}
          variant="outline"
          size="sm"
        >
          {t('joinInputSubmit')}
        </LinkButton>
      )}
    </li>
  )
}
