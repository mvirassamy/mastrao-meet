import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MeetingWeekStrip } from './MeetingWeekStrip'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        'dashboard.calendar.label': 'Meeting calendar',
        'dashboard.calendar.previousWeek': 'Previous week',
        'dashboard.calendar.nextWeek': 'Next week',
        'dashboard.calendar.today': 'Today',
      })[key] ?? key,
    i18n: { language: 'en', resolvedLanguage: 'en' },
  }),
}))

afterEach(cleanup)

describe('MeetingWeekStrip', () => {
  const selectedDate = new Date(Date.UTC(2026, 8, 26, 12))

  it('selects a day and moves by complete weeks', () => {
    const onSelectDate = vi.fn()
    render(
      <MeetingWeekStrip
        selectedDate={selectedDate}
        today={selectedDate}
        onSelectDate={onSelectDate}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /Sunday.*27/i }))
    expect(onSelectDate.mock.calls[0][0].toISOString()).toBe(
      '2026-09-27T12:00:00.000Z'
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next week' }))
    expect(onSelectDate.mock.calls[1][0].toISOString()).toBe(
      '2026-10-03T12:00:00.000Z'
    )
  })

  it('hides "Today" while today is selected', () => {
    render(
      <MeetingWeekStrip
        selectedDate={selectedDate}
        today={selectedDate}
        onSelectDate={vi.fn()}
      />
    )
    expect(screen.queryByRole('button', { name: 'Today' })).toBeNull()
  })

  it('brings the calendar back to today and moves focus to it', () => {
    const today = new Date(Date.UTC(2026, 8, 26, 12))
    const onSelectDate = vi.fn()
    const { rerender } = render(
      <MeetingWeekStrip
        selectedDate={new Date(Date.UTC(2026, 9, 16, 12))}
        today={today}
        onSelectDate={onSelectDate}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Today' }))
    expect(onSelectDate).toHaveBeenCalledWith(today)

    rerender(
      <MeetingWeekStrip
        selectedDate={today}
        today={today}
        onSelectDate={onSelectDate}
      />
    )
    expect(screen.queryByRole('button', { name: 'Today' })).toBeNull()
    expect(document.activeElement?.getAttribute('aria-label')).toMatch(
      /Saturday.*26/i
    )
  })
})
