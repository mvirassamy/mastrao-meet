import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Router } from 'wouter'
import { memoryLocation } from 'wouter/memory-location'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ApiUser } from '@/features/auth/api/ApiUser'
import { AuthenticatedHome } from './AuthenticatedHome'

const TODAY = new Date(Date.UTC(2026, 9, 4, 12))
const PAST_DAY = new Date(Date.UTC(2026, 9, 1, 12))

vi.mock('../hooks/useCalendarToday', () => ({
  useCalendarToday: () => TODAY,
}))
vi.mock('../components/authenticated/MeetWorkspaceShell', () => ({
  MeetWorkspaceShell: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}))
vi.mock('../components/authenticated/MeetWorkspaceToolbar', () => ({
  MeetWorkspaceToolbar: () => null,
}))
vi.mock('../components/authenticated/MeetingWeekStrip', () => ({
  MeetingWeekStrip: ({
    onSelectDate,
  }: {
    onSelectDate: (date: Date) => void
  }) => (
    <>
      <button type="button" onClick={() => onSelectDate(PAST_DAY)}>
        past
      </button>
      <button type="button" onClick={() => onSelectDate(TODAY)}>
        today
      </button>
    </>
  ),
}))
vi.mock('../components/authenticated/DayMeetings', () => ({
  DayMeetings: ({ day, position }: { day: string; position: string }) => (
    <p>{`${day} ${position}`}</p>
  ),
}))

const user = { timezone: 'Europe/Paris' } as ApiUser

const renderHome = (path: string) => {
  const location = memoryLocation({ path, record: true })
  render(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <AuthenticatedHome user={user} />
    </Router>
  )
  return location
}

afterEach(cleanup)

describe('authenticated home selected day', () => {
  it('shows today without a day in the address', () => {
    renderHome('/')
    expect(screen.getByText('2026-10-04 today')).toBeTruthy()
  })

  it('keeps the selected day in the address, so coming back restores it', () => {
    const location = renderHome('/')
    fireEvent.click(screen.getByRole('button', { name: 'past' }))

    expect(screen.getByText('2026-10-01 past')).toBeTruthy()
    expect(location.history.at(-1)).toBe('/?jour=2026-10-01')

    cleanup()
    renderHome('/?jour=2026-10-01')
    expect(screen.getByText('2026-10-01 past')).toBeTruthy()
  })

  it('drops the day from the address when today is selected again', () => {
    const location = renderHome('/?jour=2026-10-01')
    fireEvent.click(screen.getByRole('button', { name: 'today' }))

    expect(screen.getByText('2026-10-04 today')).toBeTruthy()
    expect(location.history.at(-1)).toBe('/')
  })

  it('ignores an invalid day in the address', () => {
    renderHome('/?jour=2026-02-30')
    expect(screen.getByText('2026-10-04 today')).toBeTruthy()
  })
})
