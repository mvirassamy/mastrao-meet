import { useCallback, useState } from 'react'
import type { ApiUser } from '@/features/auth/api/ApiUser'
import { dayKey } from '@/features/meetingHistory/utils/meetingHistoryFormat'
import { Screen } from '@/layout/Screen'
import {
  DayMeetings,
  type DayPosition,
} from '../components/authenticated/DayMeetings'
import { MeetWorkspaceShell } from '../components/authenticated/MeetWorkspaceShell'
import { MeetWorkspaceToolbar } from '../components/authenticated/MeetWorkspaceToolbar'
import { MeetingWeekStrip } from '../components/authenticated/MeetingWeekStrip'
import { isSameCalendarDay } from '../utils/authenticatedHomeDate'
import { useCalendarToday } from '../hooks/useCalendarToday'

type AuthenticatedHomeProps = {
  user: ApiUser
}

/** Calendar dates are stored at noon UTC: their UTC day is the shown day. */
const calendarDayKey = (date: Date) => dayKey(date, 'UTC')

const dayPosition = (selected: string, today: string): DayPosition => {
  if (selected < today) return 'past'
  if (selected > today) return 'future'
  return 'today'
}

export const AuthenticatedHome = ({ user }: AuthenticatedHomeProps) => {
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
  const selectedDay = calendarDayKey(effectiveSelectedDate)

  return (
    <Screen header={false} footer={false}>
      <MeetWorkspaceShell user={user} toolbar={<MeetWorkspaceToolbar />}>
        <MeetingWeekStrip
          selectedDate={effectiveSelectedDate}
          today={today}
          onSelectDate={setSelectedDate}
        />
        <DayMeetings
          day={selectedDay}
          position={dayPosition(selectedDay, calendarDayKey(today))}
          timeZone={user.timezone}
        />
      </MeetWorkspaceShell>
    </Screen>
  )
}
