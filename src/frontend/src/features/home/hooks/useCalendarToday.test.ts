import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useCalendarToday } from './useCalendarToday'

describe('useCalendarToday', () => {
  afterEach(() => vi.useRealTimers())

  it('refreshes today after midnight and reports the rollover', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-26T23:59:30.000Z'))
    const onDayChange = vi.fn()
    const { result } = renderHook(() => useCalendarToday('UTC', onDayChange))

    expect(result.current.toISOString()).toBe('2026-09-26T12:00:00.000Z')

    act(() => vi.advanceTimersByTime(60_000))

    expect(result.current.toISOString()).toBe('2026-09-27T12:00:00.000Z')
    expect(onDayChange).toHaveBeenCalledTimes(1)
  })
})
