import { describe, expect, it } from 'vitest'
import { normalizeTime, timeSlots } from './timeSlots'

describe('timeSlots', () => {
  it('offers every quarter hour of the day without a start', () => {
    const slots = timeSlots()
    expect(slots).toHaveLength(96)
    expect(slots[0]).toEqual({ time: '00:00', duration: null })
    expect(slots[95]).toEqual({ time: '23:45', duration: null })
  })

  it('offers the later slots with their duration after a start', () => {
    expect(timeSlots('10:00').slice(0, 4)).toEqual([
      { time: '10:15', duration: 15 },
      { time: '10:30', duration: 30 },
      { time: '10:45', duration: 45 },
      { time: '11:00', duration: 60 },
    ])
  })

  it('starts at the next quarter hour after an off-grid start', () => {
    expect(timeSlots('10:07')[0]).toEqual({ time: '10:15', duration: 8 })
  })

  it('offers nothing after the last slot of the day', () => {
    expect(timeSlots('23:45')).toEqual([])
  })

  it('ignores an invalid start', () => {
    expect(timeSlots('25:00')[0]).toEqual({ time: '00:00', duration: null })
  })

  it('leaves out the slots before the earliest time', () => {
    expect(timeSlots(undefined, '19:37')[0]).toEqual({
      time: '19:45',
      duration: null,
    })
    expect(timeSlots('20:00', '19:37')[0]).toEqual({
      time: '20:15',
      duration: 15,
    })
  })
})

describe('normalizeTime', () => {
  it.each([
    ['9', '09:00'],
    ['9h', '09:00'],
    ['9h30', '09:30'],
    ['9:30', '09:30'],
    [' 14 h 05 ', '14:05'],
    ['10:00', '10:00'],
  ])('reads %s as %s', (typed, time) => {
    expect(normalizeTime(typed)).toBe(time)
  })

  it.each(['24h', '9h75', 'midi', ''])(
    'leaves %s for the form to reject',
    (typed) => {
      expect(normalizeTime(typed)).toBe(typed)
    }
  )
})
