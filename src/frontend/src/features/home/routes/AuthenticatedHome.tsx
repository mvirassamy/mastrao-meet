import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { ApiUser } from '@/features/auth/api/ApiUser'
import { Screen } from '@/layout/Screen'
import { css } from '@/styled-system/css'
import { CreateMeetingMenu } from '../components/CreateMeetingMenu'
import { MeetWorkspaceShell } from '../components/authenticated/MeetWorkspaceShell'
import { MeetWorkspaceToolbar } from '../components/authenticated/MeetWorkspaceToolbar'
import { MeetingWeekStrip } from '../components/authenticated/MeetingWeekStrip'
import { isSameCalendarDay } from '../utils/authenticatedHomeDate'
import { useCalendarToday } from '../hooks/useCalendarToday'

type AuthenticatedHomeProps = {
  user: ApiUser
}

export const AuthenticatedHome = ({ user }: AuthenticatedHomeProps) => {
  const { t } = useTranslation('home')
  const [selectedDate, setSelectedDate] = useState<Date | null>(null)
  const handleDayChange = useCallback((previous: Date, next: Date) => {
    setSelectedDate((selected) =>
      selected === null || isSameCalendarDay(selected, previous)
        ? next
        : selected
    )
  }, [])
  const today = useCalendarToday(user.timezone, handleDayChange)
  const effectiveSelectedDate = selectedDate ?? today
  const isToday = isSameCalendarDay(effectiveSelectedDate, today)

  return (
    <Screen header={false} footer={false}>
      <MeetWorkspaceShell user={user} toolbar={<MeetWorkspaceToolbar />}>
        <MeetingWeekStrip
          selectedDate={effectiveSelectedDate}
          today={today}
          onSelectDate={setSelectedDate}
        />

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
              {isToday
                ? t('dashboard.empty.todayTitle')
                : t('dashboard.empty.dateTitle')}
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
      </MeetWorkspaceShell>
    </Screen>
  )
}
