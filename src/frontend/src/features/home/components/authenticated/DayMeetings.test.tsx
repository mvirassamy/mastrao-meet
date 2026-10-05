import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import { meetingBackPath } from '@/features/meetingHistory/utils/meetingOrigin'
import { DayMeetings, type DayPosition } from './DayMeetings'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count?: number }) =>
      options?.count === undefined ? key : `${key}:${options.count}`,
    i18n: { language: 'fr', resolvedLanguage: 'fr' },
  }),
}))

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))

vi.mock('../CreateMeetingMenu', () => ({
  CreateMeetingMenu: ({ label }: { label: string }) => (
    <button type="button">{label}</button>
  ),
}))

const fetchApiMock = vi.mocked(fetchApi)

const meeting = (id: string, startedAt: string) => ({
  id,
  title: `Réunion ${id}`,
  started_at: startedAt,
  ended_at: null,
  summary_status: 'available',
  transcript_status: 'available',
})

const mockHistoryFetch = (read: (url: string) => Promise<unknown>) =>
  fetchApiMock.mockImplementation(async (url) => {
    if (url.startsWith('meetings/?')) return { results: [], next_cursor: null }
    return read(url)
  })

const FIRST_PAGE = 'meetings/history/'
const OLDER_PAGE = 'meetings/history/?cursor=page-2'

const renderDay = (
  day: string,
  position: DayPosition = 'past',
  client = new QueryClient({
    defaultOptions: { queries: { retryDelay: 0 } },
  })
) => {
  render(
    <QueryClientProvider client={client}>
      <DayMeetings day={day} position={position} timeZone="Europe/Paris" />
    </QueryClientProvider>
  )
  return client
}

const callsTo = (url: string) =>
  fetchApiMock.mock.calls.filter(([called]) => called === url).length

beforeEach(() => {
  fetchApiMock.mockReset()
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  window.history.replaceState(null, '', '/')
})

describe('meetings of the selected home day', () => {
  it('restores focus after returning from detail when planned meetings arrive later', async () => {
    const history = {
      results: [meeting('du-jour', '2026-10-01T08:00:00Z')],
      next_cursor: null,
    }
    mockHistoryFetch(async () => history)
    window.history.replaceState(null, '', '/?jour=2026-10-01')
    const client = renderDay('2026-10-01')
    fireEvent.click(
      await screen.findByRole('link', { name: /Réunion du-jour/ })
    )
    const returnPath = meetingBackPath(window.history.state)
    expect(returnPath).toBe('/?jour=2026-10-01')
    cleanup()

    client.removeQueries({ queryKey: ['plannedMeetings'] })
    let finishPlanned: (value: {
      results: never[]
      next_cursor: null
    }) => void = () => undefined
    const delayedPlanned = new Promise((resolve) => {
      finishPlanned = resolve
    })
    fetchApiMock.mockImplementation(async (url) => {
      if (url.startsWith('meetings/?')) return delayedPlanned
      return history
    })
    window.history.replaceState(null, '', returnPath)
    renderDay('2026-10-01', 'past', client)
    expect(screen.getByRole('status')).toBeTruthy()
    expect(screen.queryByRole('link', { name: /Réunion du-jour/ })).toBeNull()

    finishPlanned({ results: [], next_cursor: null })
    const restoredLink = await screen.findByRole('link', {
      name: /Réunion du-jour/,
    })
    await waitFor(() => expect(document.activeElement).toBe(restoredLink))
  })

  it('loads older history pages until the day is covered', async () => {
    mockHistoryFetch(async (url: string) => {
      if (url === FIRST_PAGE)
        return {
          results: [meeting('recente', '2026-10-04T08:00:00Z')],
          next_cursor: 'page-2',
        }
      if (url === OLDER_PAGE)
        return {
          results: [
            meeting('du-jour', '2026-10-01T08:00:00Z'),
            meeting('ancienne', '2026-09-28T08:00:00Z'),
          ],
          next_cursor: 'page-3',
        }
      throw new Error(`unexpected ${url}`)
    })
    renderDay('2026-10-01')

    expect(
      await screen.findByRole('link', { name: /Réunion du-jour/ })
    ).toBeTruthy()
    expect(screen.queryByRole('link', { name: /Réunion recente/ })).toBeNull()
    expect(callsTo(OLDER_PAGE)).toBe(1)
    expect(fetchApiMock).not.toHaveBeenCalledWith(
      'meetings/history/?cursor=page-3'
    )
  })

  it('keeps the retry button and its focus while a failed page is retried', async () => {
    let retrying = false
    let failRetry: (error: Error) => void = () => undefined
    mockHistoryFetch(async (url: string) => {
      if (url === FIRST_PAGE)
        return {
          results: [meeting('recente', '2026-10-04T08:00:00Z')],
          next_cursor: 'page-2',
        }
      if (!retrying) throw new Error('unavailable')
      return new Promise((_, reject) => {
        failRetry = reject
      })
    })
    renderDay('2026-10-01')

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(callsTo(OLDER_PAGE)).toBeLessThanOrEqual(2)

    retrying = true
    const retry = screen.getByRole('button', { name: 'error.retry' })
    retry.focus()
    fireEvent.click(retry)
    await waitFor(() => expect(retry.hasAttribute('data-pending')).toBe(true))
    expect(document.activeElement).toBe(retry)
    // The spinner replaces the retry icon while the page is fetched again.
    expect(retry.querySelector('svg')).toBeNull()

    // The automatic retry of the failed page fails at once.
    retrying = false
    failRetry(new Error('still unavailable'))
    await waitFor(() => expect(retry.hasAttribute('data-pending')).toBe(false))
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(document.activeElement).toBe(retry)
    expect(retry.querySelector('svg')).not.toBeNull()
  })

  it.each([
    ['past', 'dashboard.empty.pastTitle'],
    ['today', 'dashboard.empty.todayTitle'],
    ['future', 'dashboard.empty.dateTitle'],
  ] as const)('titles an empty %s day', async (position, title) => {
    fetchApiMock.mockResolvedValue({ results: [], next_cursor: null })
    renderDay('2026-10-02', position)

    expect(await screen.findByRole('heading', { name: title })).toBeTruthy()
  })

  it('lists joinable planned meetings above now and the others below', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-05T16:51:00Z'))
    const planned = (name: string, state: string, start: string) => ({
      meeting_ref: `meeting_${name}_0123456789`,
      room_ref: 'room_0123456789abcdef0123456789abcdef',
      title: `Réunion ${name}`,
      scheduled_start_at: Date.parse(start) / 1000,
      scheduled_end_at: Date.parse(start) / 1000 + 15 * 60,
      timezone: 'Europe/Paris',
      state,
    })
    fetchApiMock.mockImplementation(async (url) => {
      if (url.startsWith('meetings/?'))
        return {
          results: [
            planned('suivante', 'ready', '2026-10-05T17:00:00Z'),
            planned('fermee', 'ended', '2026-10-05T16:45:00Z'),
          ],
          next_cursor: null,
        }
      return {
        results: [meeting('passee', '2026-10-05T16:09:00Z')],
        next_cursor: null,
      }
    })
    renderDay('2026-10-05', 'today')

    const upcoming = await screen.findByRole('region', {
      name: 'dashboard.meetings.upcoming',
    })
    const finished = screen.getByRole('region', {
      name: 'dashboard.meetings.finished',
    })
    expect(within(upcoming).getByText('Réunion suivante')).toBeTruthy()
    expect(within(finished).getByText('Réunion fermee')).toBeTruthy()
    expect(
      within(finished).getByRole('link', { name: /Réunion passee/ })
    ).toBeTruthy()
    expect(
      screen.getByRole('separator', { name: 'dashboard.meetings.now' })
    ).toBeTruthy()
  })
})
