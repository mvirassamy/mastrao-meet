import { useSearchParams } from 'wouter'
import type { ApiUser } from '@/features/auth/api/ApiUser'
import { Screen } from '@/layout/Screen'
import {
  DayMeetings,
  type DayPosition,
} from '../components/authenticated/DayMeetings'
import { MeetWorkspaceShell } from '../components/authenticated/MeetWorkspaceShell'
import { MeetWorkspaceToolbar } from '../components/authenticated/MeetWorkspaceToolbar'
import { MeetingWeekStrip } from '../components/authenticated/MeetingWeekStrip'
import {
  calendarDateFromKey,
  calendarDayKey,
} from '../utils/authenticatedHomeDate'
import { useCalendarToday } from '../hooks/useCalendarToday'

/**
 * Selected day in the address, so going back from a meeting returns to it.
 * Without it the home follows today, even across midnight.
 */
const DAY_PARAM = 'jour'

type AuthenticatedHomeProps = {
  user: ApiUser
}

const dayPosition = (selected: string, today: string): DayPosition => {
  if (selected < today) return 'past'
  if (selected > today) return 'future'
  return 'today'
}

export const AuthenticatedHome = ({ user }: AuthenticatedHomeProps) => {
  const [searchParams, setSearchParams] = useSearchParams()
  const today = useCalendarToday(user.timezone)
  const todayKey = calendarDayKey(today)
  const selectedDate = calendarDateFromKey(searchParams.get(DAY_PARAM)) ?? today
  const selectedDay = calendarDayKey(selectedDate)

  const selectDate = (date: Date) => {
    const day = calendarDayKey(date)
    setSearchParams(
      (params) => {
        if (day === todayKey) params.delete(DAY_PARAM)
        else params.set(DAY_PARAM, day)
        return params
      },
      { replace: true }
    )
  }

  return (
    <Screen header={false} footer={false}>
      <MeetWorkspaceShell user={user} toolbar={<MeetWorkspaceToolbar />}>
        <MeetingWeekStrip
          selectedDate={selectedDate}
          today={today}
          onSelectDate={selectDate}
        />
        <DayMeetings
          day={selectedDay}
          position={dayPosition(selectedDay, todayKey)}
          timeZone={user.timezone}
        />
      </MeetWorkspaceShell>
    </Screen>
  )
}
