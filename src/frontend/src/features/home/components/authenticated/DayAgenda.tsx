import type { RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import type { MeetingHistoryItem } from '@/features/meetingHistory/api/types'
import { formatMeetingTimeRange } from '@/features/meetingHistory/utils/meetingHistoryFormat'
import type { PlannedMeeting } from '../../api/plannedMeetings'
import { useCurrentMinute } from '../../hooks/useCurrentMinute'
import { DayMeetingEvent } from './DayMeetingEvent'
import { ScheduledMeetingEvent } from './ScheduledMeetingEvent'

export type DayEvent =
  | { kind: 'history'; item: MeetingHistoryItem; startsAt: Date }
  | { kind: 'planned'; item: PlannedMeeting; startsAt: Date }

/**
 * Accent of each event, in turn, so that neighbouring meetings never share
 * a colour. Dark enough for the time printed in the same colour.
 */
const EVENT_COLORS = [
  '#2d5be3',
  '#17784d',
  '#b3400b',
  '#6d28d9',
  '#0e6f86',
  '#b4235f',
]

/** A planned meeting can be joined until its scheduled end, unless closed. */
const isUpcoming = (event: DayEvent, now: Date) =>
  event.kind === 'planned' &&
  !event.item.isClosed &&
  event.item.endsAt.getTime() > now.getTime()

const byStart = (left: DayEvent, right: DayEvent) =>
  left.startsAt.getTime() - right.startsAt.getTime()

/**
 * Meetings still to join come first, in the order they happen; finished
 * ones follow, most recent first. On today, a line marks the current time.
 */
export const DayAgenda = ({
  events,
  showNow,
  locale,
  timeZone,
  containerRef,
}: {
  events: DayEvent[]
  showNow: boolean
  locale: string
  timeZone?: string
  containerRef: RefObject<HTMLDivElement>
}) => {
  const { t } = useTranslation('home')
  const now = useCurrentMinute()
  const upcoming = events.filter((event) => isUpcoming(event, now))
  const finished = events.filter((event) => !isUpcoming(event, now))
  upcoming.sort(byStart)
  finished.sort((left, right) => byStart(right, left))

  return (
    <div ref={containerRef}>
      <DaySection
        id="day-meetings-upcoming"
        title={t('dashboard.meetings.upcoming')}
        events={upcoming}
        firstColor={0}
        locale={locale}
        timeZone={timeZone}
        now={showNow ? now : undefined}
      />
      {showNow && <NowMarker now={now} locale={locale} timeZone={timeZone} />}
      <DaySection
        id="day-meetings-finished"
        title={t('dashboard.meetings.finished')}
        events={finished}
        firstColor={upcoming.length}
        locale={locale}
        timeZone={timeZone}
        now={showNow ? now : undefined}
      />
    </div>
  )
}

const DaySection = ({
  id,
  title,
  events,
  firstColor,
  locale,
  timeZone,
  now,
}: {
  id: string
  title: string
  events: DayEvent[]
  /** Colours continue across sections so neighbours stay distinct. */
  firstColor: number
  locale: string
  timeZone?: string
  /** Current time, on today only. */
  now?: Date
}) => {
  if (events.length === 0) return null

  return (
    <section
      aria-labelledby={id}
      className={css({ '& + &': { marginTop: '1.5rem' } })}
    >
      <div
        className={css({
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          marginBottom: '0.5rem',
          color: 'muted-foreground',
          fontSize: '0.75rem',
          lineHeight: '1rem',
          fontWeight: 600,
        })}
      >
        <h2
          id={id}
          className={css({
            margin: 0,
            font: 'inherit',
            color: 'inherit',
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
          })}
        >
          {title}
        </h2>
        <span
          className={css({
            paddingX: '0.4375rem',
            border: '1px solid token(colors.border)',
            borderRadius: 'full',
            backgroundColor: 'muted',
            fontSize: '0.6875rem',
          })}
        >
          {events.length}
        </span>
      </div>
      <ul
        className={css({
          display: 'flex',
          flexDirection: 'column',
          gap: '0.625rem',
          margin: 0,
          padding: 0,
          listStyle: 'none',
        })}
      >
        {events.map((event, index) => {
          const color = EVENT_COLORS[(firstColor + index) % EVENT_COLORS.length]
          if (event.kind === 'planned')
            return (
              <ScheduledMeetingEvent
                key={event.item.id}
                meeting={event.item}
                color={color}
                locale={locale}
                timeZone={timeZone}
                now={now}
              />
            )
          return (
            <DayMeetingEvent
              key={event.item.id}
              item={event.item}
              color={color}
              locale={locale}
              timeZone={timeZone}
            />
          )
        })}
      </ul>
    </section>
  )
}

const NowMarker = ({
  now,
  locale,
  timeZone,
}: {
  now: Date
  locale: string
  timeZone?: string
}) => {
  const { t } = useTranslation('home')
  const time = formatMeetingTimeRange(now, null, locale, timeZone)

  return (
    <div
      role="separator"
      aria-label={t('dashboard.meetings.now', { time })}
      className={css({
        display: 'flex',
        alignItems: 'center',
        gap: '0.625rem',
        marginY: '1.25rem',
      })}
    >
      <span
        aria-hidden="true"
        className={css({
          color: 'danger.subtle-text',
          fontSize: '0.75rem',
          fontWeight: 700,
          fontVariantNumeric: 'tabular-nums',
        })}
      >
        {time}
      </span>
      <span
        aria-hidden="true"
        className={css({
          position: 'relative',
          flex: 1,
          height: '2px',
          backgroundColor: 'danger',
          _before: {
            content: '""',
            position: 'absolute',
            left: '-4px',
            top: '-4px',
            width: '10px',
            height: '10px',
            borderRadius: 'full',
            backgroundColor: 'danger',
          },
        })}
      />
    </div>
  )
}
