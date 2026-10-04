import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRightIcon } from '@/icons'
import { css } from '@/styled-system/css'
import type { MeetingHistoryItem } from '@/features/meetingHistory/api/types'
import { MeetingContentStatusIcons } from '@/features/meetingHistory/components/MeetingContentStatusIcon'
import { MeetingParticipantsStack } from '@/features/meetingHistory/components/MeetingParticipantsStack'
import { MeetingRowLink } from '@/features/meetingHistory/components/MeetingRowLink'
import { RowOverlayCell } from '@/features/meetingHistory/components/RowOverlayCell'
import { formatMeetingTime } from '@/features/meetingHistory/utils/meetingHistoryFormat'
import { participantTotal } from '@/features/meetingHistory/utils/participants'

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
  const participantCount = participantTotal(
    item.participantCount,
    item.participantNames
  )

  return (
    <li
      style={{ borderLeftColor: color }}
      className={css({
        position: 'relative',
        display: 'grid',
        // Below lg the time and title get the full width; the statuses join
        // the participants on the line below.
        gridTemplateColumns: 'minmax(0, 1fr) auto auto',
        gridTemplateAreas: {
          base: '"main main chevron" "people status chevron"',
          lg: '"main status chevron" "people status chevron"',
        },
        alignItems: 'center',
        columnGap: '1rem',
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
      <div className={css({ gridArea: 'main', minWidth: 0 })}>
        <p
          style={{ color }}
          className={css({
            margin: 0,
            marginBottom: '0.125rem',
            fontSize: '0.8125rem',
            lineHeight: '1.25rem',
            fontWeight: 600,
          })}
        >
          {formatMeetingTime(item.startedAt, item.endedAt, locale, timeZone)}
        </p>
        <MeetingRowLink item={item} linkRef={linkRef} />
      </div>
      {participantCount > 0 && (
        <div
          className={css({
            gridArea: 'people',
            display: 'flex',
            minWidth: 0,
            marginTop: '0.5rem',
          })}
        >
          <RowOverlayCell linkRef={linkRef}>
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
                whiteSpace: 'nowrap',
              })}
            >
              {t('participants', { count: participantCount })}
            </span>
          </RowOverlayCell>
        </div>
      )}
      {/* Margins rather than a row gap: a meeting without participants keeps
          a single line on wide screens. */}
      <div
        className={css({
          gridArea: 'status',
          marginTop: { base: '0.5rem', lg: 0 },
        })}
      >
        <MeetingContentStatusIcons item={item} linkRef={linkRef} />
      </div>
      <ChevronRightIcon
        size={18}
        aria-hidden="true"
        className={css({ gridArea: 'chevron', color: 'muted-foreground' })}
      />
    </li>
  )
}
