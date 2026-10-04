import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
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

const FIRST_PAGE = 'meetings/history/'
const OLDER_PAGE = 'meetings/history/?cursor=page-2'

const renderDay = (day: string, position: DayPosition = 'past') => {
  const client = new QueryClient({
    defaultOptions: { queries: { retryDelay: 0 } },
  })
  render(
    <QueryClientProvider client={client}>
      <DayMeetings day={day} position={position} timeZone="Europe/Paris" />
    </QueryClientProvider>
  )
}

const callsTo = (url: string) =>
  fetchApiMock.mock.calls.filter(([called]) => called === url).length

beforeEach(() => {
  fetchApiMock.mockReset()
})
afterEach(cleanup)

describe('meetings of the selected home day', () => {
  it('loads older history pages until the day is covered', async () => {
    fetchApiMock.mockImplementation(async (url: string) => {
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
    fetchApiMock.mockImplementation(async (url: string) => {
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

    // The automatic retry of the failed page fails at once.
    retrying = false
    failRetry(new Error('still unavailable'))
    await waitFor(() => expect(retry.hasAttribute('data-pending')).toBe(false))
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(document.activeElement).toBe(retry)
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
})
