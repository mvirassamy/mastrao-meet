import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Route, Router, Switch } from 'wouter'
import { memoryLocation } from 'wouter/memory-location'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import { MEETING_HISTORY_PATH } from '../paths'
import { MeetingHistoryDetailView } from './MeetingHistoryDetailView'
import { MeetingHistoryList } from './MeetingHistoryList'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count?: number }) =>
      options?.count === undefined ? key : `${key}:${options.count}`,
    i18n: { language: 'fr', resolvedLanguage: 'fr' },
  }),
}))

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))

vi.mock('@/features/home/components/CreateMeetingMenu', () => ({
  CreateMeetingMenu: ({ label }: { label: string }) => (
    <button type="button">{label}</button>
  ),
}))

const fetchApiMock = vi.mocked(fetchApi)

const item = (id: string, startedAt: string, overrides = {}) => ({
  id,
  title: `Réunion ${id}`,
  started_at: startedAt,
  ended_at: null,
  summary_status: 'available',
  transcript_status: 'available',
  ...overrides,
})

const renderHistory = (path = MEETING_HISTORY_PATH) => {
  const location = memoryLocation({ path, record: true })
  const client = new QueryClient({
    defaultOptions: { queries: { retryDelay: 0 } },
  })
  render(
    <QueryClientProvider client={client}>
      <Router hook={location.hook}>
        <Switch>
          <Route path={MEETING_HISTORY_PATH}>
            <MeetingHistoryList timeZone="Europe/Paris" />
          </Route>
          <Route<{ meetingId: string }>
            path={`${MEETING_HISTORY_PATH}/:meetingId`}
          >
            {({ meetingId }) => (
              <MeetingHistoryDetailView
                meetingId={meetingId}
                timeZone="Europe/Paris"
              />
            )}
          </Route>
        </Switch>
      </Router>
    </QueryClientProvider>
  )
  return location
}

beforeEach(() => {
  fetchApiMock.mockReset()
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('meeting history flow', () => {
  it('lists past meetings from most recent, opens a detail and returns', async () => {
    fetchApiMock.mockImplementation(async (url: string) => {
      if (url === 'meetings/history/')
        return {
          results: [
            item('ancienne', '2026-09-01T08:00:00Z'),
            item('recente', '2026-09-20T08:00:00Z', {
              summary_status: 'processing',
            }),
          ],
          next_cursor: null,
        }
      if (url === 'meetings/history/recente/')
        return {
          ...item('recente', '2026-09-20T08:00:00Z'),
          summary: { status: 'processing' },
          transcript: {
            status: 'available',
            segments: [
              { id: 's1', start_ms: 3000, speaker: 'Camille', text: 'Bonjour' },
            ],
          },
        }
      throw new ApiError(404, {})
    })
    const location = renderHistory()

    const links = await screen.findAllByRole('link')
    expect(links.map((link) => link.textContent)).toEqual([
      'Réunion recente',
      'Réunion ancienne',
    ])
    expect(screen.getByText('status.summary.transcribing')).toBeTruthy()

    fireEvent.click(links[0])
    const heading = await screen.findByRole('heading', {
      level: 1,
      name: 'Réunion recente',
    })
    await waitFor(() => expect(document.activeElement).toBe(heading))
    expect(screen.getByRole('region', { name: 'summary.title' })).toBeTruthy()
    expect(
      screen.getByRole('region', { name: 'transcript.title' })
    ).toBeTruthy()
    expect(screen.getByText('summary.transcribing.title')).toBeTruthy()
    expect(screen.getByText('Bonjour')).toBeTruthy()
    expect(screen.getByText('00:03')).toBeTruthy()

    fireEvent.click(screen.getByRole('link', { name: 'detail.back' }))
    expect(location.history?.at(-1)).toBe(MEETING_HISTORY_PATH)
    const restored = await screen.findByRole('link', {
      name: 'Réunion recente',
    })
    await waitFor(() => expect(document.activeElement).toBe(restored))
  })

  it('shows the empty state', async () => {
    fetchApiMock.mockResolvedValue({ results: [], next_cursor: null })
    renderHistory()
    expect(await screen.findByText('empty.title')).toBeTruthy()
  })

  it('keeps polling while an automatic summary has not started', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date('2026-09-20T09:00:00Z'))
    let summaryStatus = 'absent'
    fetchApiMock.mockImplementation(async () => ({
      results: [
        item('recente', '2026-09-20T08:00:00Z', {
          summary_status: summaryStatus,
        }),
      ],
      next_cursor: null,
    }))
    renderHistory()

    expect(await screen.findByText('status.summary.not_started')).toBeTruthy()
    summaryStatus = 'available'
    await vi.advanceTimersByTimeAsync(15_000)

    expect(await screen.findByText('status.summary.available')).toBeTruthy()
    expect(fetchApiMock).toHaveBeenCalledTimes(2)
  })

  it('labels partial content and exposes concise live status updates', async () => {
    const digest = 'a'.repeat(64)
    fetchApiMock.mockResolvedValue({
      ...item('partielle', '2026-09-20T08:00:00Z'),
      summary_projection: {
        version: 1,
        state: 'available',
        revision: 2,
        digest,
        source: { revision: 2, digest },
      },
      transcript_projection: {
        version: 1,
        state: 'partial',
        revision: 2,
        digest,
        source: null,
      },
      summary: { status: 'available', text: 'Résumé.' },
      transcript: {
        status: 'available',
        segments: [{ id: 's1', start_ms: 0, speaker: 'A', text: 'Extrait' }],
      },
    })
    renderHistory(`${MEETING_HISTORY_PATH}/partielle`)

    expect(await screen.findByText('transcript.partial')).toBeTruthy()
    expect(
      screen
        .getAllByRole('status')
        .some((status) => status.textContent === 'status.transcript.partial')
    ).toBe(true)
  })

  it('shows an error with a working retry', async () => {
    fetchApiMock.mockRejectedValue(new ApiError(503, {}))
    renderHistory()
    expect(await screen.findByText('error.title')).toBeTruthy()

    fetchApiMock.mockReset()
    fetchApiMock.mockResolvedValue({ results: [], next_cursor: null })
    fireEvent.click(screen.getByRole('button', { name: 'error.retry' }))
    expect(await screen.findByText('empty.title')).toBeTruthy()
  })

  it('loads older meetings on demand', async () => {
    fetchApiMock.mockImplementation(async (url: string) =>
      url.includes('cursor=page-2')
        ? {
            results: [item('plus-ancienne', '2026-08-01T08:00:00Z')],
            next_cursor: null,
          }
        : {
            results: [item('recente', '2026-09-20T08:00:00Z')],
            next_cursor: 'page-2',
          }
    )
    renderHistory()
    fireEvent.click(await screen.findByRole('button', { name: 'loadMore' }))
    expect(
      await screen.findByRole('link', { name: 'Réunion plus-ancienne' })
    ).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'loadMore' })).toBeNull()
  })

  it('distinguishes a missing meeting from absent and failed content', async () => {
    fetchApiMock.mockRejectedValueOnce(new ApiError(404, {}))
    renderHistory(`${MEETING_HISTORY_PATH}/inconnue`)
    expect(await screen.findByText('detail.notFound.title')).toBeTruthy()
    expect(fetchApiMock).toHaveBeenCalledTimes(1)
    cleanup()

    fetchApiMock.mockResolvedValue({
      ...item('vide', '2026-09-20T08:00:00Z'),
      summary: { status: 'absent' },
      transcript: { status: 'failed' },
    })
    renderHistory(`${MEETING_HISTORY_PATH}/vide`)
    expect(await screen.findByText('summary.not_started.title')).toBeTruthy()
    expect(screen.getByText('transcript.failed.title')).toBeTruthy()
  })
})
