import { useRef } from 'react'
import { ChevronRightIcon } from '@/icons'
import { css } from '@/styled-system/css'
import type { MeetingHistoryItem } from '../api/types'
import { formatMeetingTime } from '../utils/meetingHistoryFormat'
import { MeetingContentStatusIcons } from './MeetingContentStatusIcon'
import { MeetingParticipantsStack } from './MeetingParticipantsStack'
import { MeetingRowLink } from './MeetingRowLink'
import { RowOverlayCell } from './RowOverlayCell'

/**
 * Meetings of one day as rows: title, time, participants, summary and
 * transcript status. The caller draws the surrounding card.
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

const MeetingHistoryRow = ({
  item,
  locale,
  timeZone,
}: {
  item: MeetingHistoryItem
  locale: string
  timeZone?: string
}) => {
  const linkRef = useRef<HTMLAnchorElement>(null)

  return (
    <li
      className={css({
        position: 'relative',
        display: 'grid',
        // Below lg the title gets the full width; participants and statuses
        // go on a second line. On wide screens the participants start in the
        // middle of the row.
        gridTemplateColumns: {
          base: 'minmax(0, 1fr) auto auto',
          lg: 'minmax(0, 1fr) minmax(5.5rem, 1fr) auto auto',
        },
        gridTemplateAreas: {
          base: '"title title chevron" "people status chevron"',
          lg: '"title people status chevron"',
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
      <div className={css({ gridArea: 'title', minWidth: 0 })}>
        <MeetingRowLink item={item} linkRef={linkRef} />
        <p
          className={css({
            marginTop: '0.125rem',
            marginBottom: 0,
            color: 'muted-foreground',
            fontSize: '0.8125rem',
            lineHeight: '1.25rem',
          })}
        >
          {formatMeetingTime(item.startedAt, item.endedAt, locale, timeZone)}
        </p>
      </div>
      <div className={css({ gridArea: 'people', justifySelf: 'start' })}>
        <RowOverlayCell linkRef={linkRef}>
          <MeetingParticipantsStack
            count={item.participantCount}
            names={item.participantNames}
          />
        </RowOverlayCell>
      </div>
      <div className={css({ gridArea: 'status' })}>
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
