import { cleanup, fireEvent, render, screen } from '@testing-library/react'
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

  it('shows an error when an older page fails, and retries that page', async () => {
    fetchApiMock.mockImplementation(async (url: string) => {
      if (url === FIRST_PAGE)
        return {
          results: [meeting('recente', '2026-10-04T08:00:00Z')],
          next_cursor: 'page-2',
        }
      throw new Error('unavailable')
    })
    renderDay('2026-10-01')

    expect(await screen.findByRole('alert')).toBeTruthy()
    const failedCalls = callsTo(OLDER_PAGE)
    expect(failedCalls).toBeLessThanOrEqual(2)

    fireEvent.click(screen.getByRole('button', { name: 'error.retry' }))
    await screen.findByRole('alert')
    expect(callsTo(OLDER_PAGE)).toBeGreaterThan(failedCalls)
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
