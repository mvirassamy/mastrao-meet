import { parseDate } from '@internationalized/date'
import { describe, expect, it } from 'vitest'
import { dateShortcuts } from './dateShortcuts'

const picks = (today: string, minDate?: string) =>
  dateShortcuts(parseDate(today), minDate ? parseDate(minDate) : undefined).map(
    ({ id, date }) => `${id} ${date.toString()}`
  )

describe('dateShortcuts', () => {
  it('offers today, tomorrow, Friday and next Monday from a Monday', () => {
    expect(picks('2026-10-05')).toEqual([
      'today 2026-10-05',
      'tomorrow 2026-10-06',
      'friday 2026-10-09',
      'nextMonday 2026-10-12',
    ])
  })

  it('does not repeat Friday when it is already tomorrow', () => {
    expect(picks('2026-10-08')).toEqual([
      'today 2026-10-08',
      'tomorrow 2026-10-09',
      'nextMonday 2026-10-12',
    ])
  })

  it('offers the next Friday and drops a repeated Monday on a Sunday', () => {
    expect(picks('2026-10-11')).toEqual([
      'today 2026-10-11',
      'tomorrow 2026-10-12',
      'friday 2026-10-16',
    ])
  })

  it('never offers a date before the minimum', () => {
    expect(picks('2026-10-05', '2026-10-07')).toEqual([
      'friday 2026-10-09',
      'nextMonday 2026-10-12',
    ])
  })
})
