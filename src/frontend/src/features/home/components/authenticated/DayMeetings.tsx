import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { FALLBACK_LANGUAGE } from '@/i18n/languageDetection'
import { RetryIcon } from '@/icons'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import { useMeetingsOfDay } from '@/features/meetingHistory/api/useMeetingsOfDay'
import { MeetingHistorySkeleton } from '@/features/meetingHistory/components/MeetingHistorySkeleton'
import { MeetingHistoryStatePanel } from '@/features/meetingHistory/components/MeetingHistoryStatePanel'
import { useRestoreOpenedMeetingFocus } from '@/features/meetingHistory/utils/focusReturn'
import { CreateMeetingMenu } from '../CreateMeetingMenu'
import { DayMeetingEvent } from './DayMeetingEvent'

/** Position of the selected day relative to today. */
export type DayPosition = 'past' | 'today' | 'future'

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

const container = css({
  width: '100%',
  maxWidth: '56rem',
  marginX: 'auto',
  padding: { base: '1.25rem 1rem 2rem', md: '2rem 2rem 3rem' },
})

/**
 * Meetings of the day selected in the home calendar. Only past meetings
 * exist for now: they come from the meeting history.
 */
export const DayMeetings = ({
  day,
  position,
  timeZone,
}: {
  /** Selected calendar day, as YYYY-MM-DD. */
  day: string
  position: DayPosition
  timeZone?: string
}) => {
  const { t, i18n } = useTranslation(['home', 'meetingHistory'])
  const locale = i18n.resolvedLanguage || i18n.language || FALLBACK_LANGUAGE
  const meetings = useMeetingsOfDay(day, timeZone)
  const listRef = useRef<HTMLUListElement>(null)
  useRestoreOpenedMeetingFocus(
    listRef,
    meetings.status === 'ready' && meetings.items.length > 0
  )

  if (meetings.status === 'loading')
    return (
      <div className={container}>
        <MeetingHistorySkeleton
          label={t('dashboard.meetings.loading')}
          rows={3}
        />
      </div>
    )

  if (meetings.status === 'error')
    return (
      <div className={container}>
        <MeetingHistoryStatePanel
          role="alert"
          headingLevel={1}
          illustration="/assets/illustrations/historique-erreur.webp"
          title={t('dashboard.meetings.errorTitle')}
          description={t('error.description', { ns: 'meetingHistory' })}
          action={
            <Button
              variant="secondary"
              icon={<RetryIcon aria-hidden="true" />}
              // Pending keeps the button, and the keyboard focus, in place;
              // loading shows the spinner while the page is fetched again.
              isPending={meetings.retrying}
              loading={meetings.retrying}
              onPress={meetings.retry}
            >
              {t('error.retry', { ns: 'meetingHistory' })}
            </Button>
          }
        />
      </div>
    )

  if (meetings.items.length === 0)
    return <NoMeetingsOfDay position={position} />

  return (
    <section aria-labelledby="day-meetings-heading" className={container}>
      <h1
        id="day-meetings-heading"
        className={css({
          margin: 0,
          marginBottom: '0.5rem',
          color: 'muted-foreground',
          fontSize: '0.75rem',
          lineHeight: '1rem',
          fontWeight: 600,
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
        })}
      >
        {t('dashboard.meetings.count', { count: meetings.items.length })}
      </h1>
      <ul
        ref={listRef}
        className={css({
          display: 'flex',
          flexDirection: 'column',
          gap: '0.625rem',
          margin: 0,
          padding: 0,
          listStyle: 'none',
        })}
      >
        {meetings.items.map((item, index) => (
          <DayMeetingEvent
            key={item.id}
            item={item}
            color={EVENT_COLORS[index % EVENT_COLORS.length]}
            locale={locale}
            timeZone={timeZone}
          />
        ))}
      </ul>
    </section>
  )
}

const emptyTitleKey = (position: DayPosition) => {
  switch (position) {
    case 'past':
      return 'dashboard.empty.pastTitle'
    case 'today':
      return 'dashboard.empty.todayTitle'
    case 'future':
      return 'dashboard.empty.dateTitle'
  }
}

const NoMeetingsOfDay = ({ position }: { position: DayPosition }) => {
  const { t } = useTranslation('home')

  return (
    <section
      className={css({
        display: 'grid',
        placeItems: 'center',
        flex: 1,
        padding: { base: '2rem 1.25rem', md: '3rem 2rem' },
        textAlign: 'center',
      })}
    >
      <div
        className={css({
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          maxWidth: '42rem',
        })}
      >
        <img
          src="/assets/illustrations/calendrier-bg.webp"
          alt=""
          width={768}
          height={512}
          decoding="async"
          className={css({
            display: 'block',
            width: { base: '220px', md: '300px' },
            height: 'auto',
            marginBottom: '0.75rem',
            userSelect: 'none',
            pointerEvents: 'none',
          })}
        />
        <h1
          className={css({
            margin: 0,
            fontSize: { base: '1.5rem', md: '1.75rem' },
            lineHeight: 1.25,
            fontWeight: 600,
            letterSpacing: '-0.025em',
            textWrap: 'balance',
          })}
        >
          {t(emptyTitleKey(position))}
        </h1>
        <p
          className={css({
            marginTop: '0.5rem',
            marginBottom: '1.25rem',
            color: 'muted-foreground',
            fontSize: '0.875rem',
            lineHeight: '1.25rem',
            textWrap: 'balance',
          })}
        >
          {t('dashboard.empty.description')}
        </p>
        <CreateMeetingMenu
          label={t('dashboard.newMeeting')}
          showIcon
          buttonProps={{
            size: 'default',
            className: css({
              minHeight: { base: '44px', md: '38px' },
              paddingX: '1rem',
            }),
          }}
        />
      </div>
    </section>
  )
}
