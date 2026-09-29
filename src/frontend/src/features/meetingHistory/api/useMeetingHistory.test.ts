import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  adaptiveProcessingRefreshMs,
  isWithinPollingDeadline,
  shouldPollItem,
  shouldPollForContent,
} from './useMeetingHistory'

afterEach(() => vi.useRealTimers())

describe('meeting history refresh policy', () => {
  it('backs off from 5 to 60 seconds with bounded jitter', () => {
    const delays = [0, 1, 2, 3, 20].map((count) =>
      adaptiveProcessingRefreshMs(count, 1_000)
    )
    expect(delays).toEqual([5_000, 15_000, 30_000, 60_000, 60_000])
    expect(adaptiveProcessingRefreshMs(0, 0)).toBe(4_500)
    expect(adaptiveProcessingRefreshMs(3, 2_000)).toBe(66_000)
  })

  it('keeps polling when readable content is retained during an outage', () => {
    expect(shouldPollForContent('available', 'available', false, true)).toBe(
      true
    )
  })

  it('stops polling abandoned meetings without an end timestamp', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-29T12:00:00Z'))
    expect(
      shouldPollItem({
        startedAt: new Date('2026-09-27T12:00:00Z'),
        endedAt: null,
        summaryStatus: 'unknown',
        transcriptStatus: 'unknown',
        summaryProjection: {},
        transcriptProjection: {},
      })
    ).toBe(false)
  })

  it('expires detail polling even when no request succeeds', () => {
    const deadline = Date.parse('2026-09-30T12:00:00Z')
    expect(isWithinPollingDeadline(deadline, deadline - 1)).toBe(true)
    expect(isWithinPollingDeadline(deadline, deadline + 1)).toBe(false)
  })
})
