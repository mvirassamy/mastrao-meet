import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import {
  fetchPlannedMeetingsPage,
  normalizePlannedMeetingsPage,
  PlannedMeetingsContractError,
} from './plannedMeetings'
vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
const fetch = vi.mocked(fetchApi)
const meeting = {
  meeting_ref: 'meeting_0123456789abcdef0123456789abcdef',
  room_ref: 'room_0123456789abcdef0123456789abcdef',
  title: 'Réunion du 6 octobre',
  created_at: Date.parse('2026-10-05T12:00:00Z') / 1000,
  scheduled_start_at: Date.parse('2026-10-06T08:00:00Z') / 1000,
  scheduled_end_at: Date.parse('2026-10-06T09:15:00Z') / 1000,
  timezone: 'Europe/Paris',
  state: 'ready',
  ended_at: null,
}
beforeEach(() => fetch.mockReset())

describe('canonical planned day read', () => {
  it('uses persisted schedule seconds rather than the creation time', () => {
    const page = normalizePlannedMeetingsPage({
      results: [meeting],
      next_cursor: null,
    })
    expect(page.items[0]).toEqual({
      id: meeting.meeting_ref,
      roomRef: meeting.room_ref,
      title: meeting.title,
      startsAt: new Date('2026-10-06T08:00:00Z'),
      endsAt: new Date('2026-10-06T09:15:00Z'),
      isClosed: false,
    })
  })
  it.each(['cancelled', 'ending', 'ended'])(
    'never proposes access for canonical state %s',
    (state) => {
      expect(
        normalizePlannedMeetingsPage({
          results: [{ ...meeting, state }],
          next_cursor: null,
        }).items[0].isClosed
      ).toBe(true)
    }
  )
  it.each([
    {},
    { results: [{}], next_cursor: null },
    { results: [{ ...meeting, state: 'unknown' }], next_cursor: null },
    { results: [{ ...meeting, room_ref: '../unsafe' }], next_cursor: null },
  ])(
    'rejects invalid metadata instead of displaying a false empty day',
    (raw) => {
      expect(() => normalizePlannedMeetingsPage(raw)).toThrow(
        PlannedMeetingsContractError
      )
    }
  )
  it('replays the same day bounds with the opaque next cursor', async () => {
    fetch.mockResolvedValue({ results: [meeting], next_cursor: 'opaque_next' })
    await fetchPlannedMeetingsPage('2026-10-06', 'Europe/Paris', 'opaque_next')
    const params = new URLSearchParams(fetch.mock.calls[0][0].split('?')[1])
    expect(params.get('day_start')).toBe(
      String(Date.parse('2026-10-05T22:00:00Z') / 1000)
    )
    expect(params.get('day_end')).toBe(
      String(Date.parse('2026-10-06T22:00:00Z') / 1000)
    )
    expect(params.get('cursor')).toBe('opaque_next')
  })
})
