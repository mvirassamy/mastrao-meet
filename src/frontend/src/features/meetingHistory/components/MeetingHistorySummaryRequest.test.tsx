import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import { MeetingHistoryDetailView } from './MeetingHistoryDetailView'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'fr', resolvedLanguage: 'fr' },
  }),
}))

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))

vi.mock('wouter', () => ({
  Link: ({ children }: { children: React.ReactNode }) => (
    <a href="#">{children}</a>
  ),
}))

const fetchApiMock = vi.mocked(fetchApi)
const DETAIL_URL = 'meetings/history/m1/'
const SUMMARY_URL = 'meetings/history/m1/summary/'

const detail = (summary: string, transcript: string) => ({
  id: 'm1',
  title: 'Réunion m1',
  started_at: '2026-09-20T08:00:00Z',
  ended_at: '2026-09-20T08:30:00Z',
  summary_status: summary,
  transcript_status: transcript,
  summary:
    summary === 'available'
      ? { status: 'available', text: 'Résumé prêt.' }
      : { status: summary },
  transcript:
    transcript === 'available'
      ? {
          status: 'available',
          segments: [{ id: 's1', start_ms: 0, speaker: 'A', text: 'Bonjour' }],
        }
      : { status: transcript },
})

type Handlers = {
  detail: () => unknown
  summary?: () => unknown
}

const serve = (handlers: Handlers) =>
  fetchApiMock.mockImplementation(
    async (url: string, options?: RequestInit) => {
      if (url === DETAIL_URL && !options?.method) return handlers.detail()
      if (url === SUMMARY_URL && options?.method === 'POST' && handlers.summary)
        return handlers.summary()
      throw new Error(`unexpected ${options?.method ?? 'GET'} ${url}`)
    }
  )

const calls = (url: string, method?: string) =>
  fetchApiMock.mock.calls.filter(
    ([calledUrl, options]) => calledUrl === url && options?.method === method
  ).length

const renderDetail = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retryDelay: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MeetingHistoryDetailView meetingId="m1" timeZone="Europe/Paris" />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  fetchApiMock.mockReset()
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('automatic summary request', () => {
  it('asks once when the transcript is ready and the summary is absent', async () => {
    let summaryStatus = 'absent'
    serve({
      detail: () => detail(summaryStatus, 'available'),
      summary: () => {
        summaryStatus = 'processing'
        return { summary_status: 'processing' }
      },
    })
    renderDetail()

    expect(await screen.findByText('summary.transcribing.title')).toBeTruthy()
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(calls(SUMMARY_URL, 'POST')).toBe(1)
  })

  it.each([
    ['absent', 'absent', 'summary.not_started.title'],
    ['failed', 'available', 'summary.failed.title'],
    ['absent', 'processing', 'summary.waitingTranscript.title'],
    ['absent', 'failed', 'summary.not_started.title'],
  ])(
    'does not ask when summary=%s and transcript=%s',
    async (summary, transcript, message) => {
      serve({ detail: () => detail(summary, transcript) })
      renderDetail()

      // The visible state title; the header also announces it to screen readers.
      expect(await screen.findByText(message, { selector: 'p' })).toBeTruthy()
      await new Promise((resolve) => setTimeout(resolve, 50))
      expect(calls(SUMMARY_URL, 'POST')).toBe(0)
    }
  )

  it('shows a failed request without retrying it', async () => {
    serve({
      detail: () => detail('absent', 'available'),
      summary: () => {
        throw new ApiError(503, {})
      },
    })
    renderDetail()

    expect(
      await screen.findByText('summary.requestFailed.title', { selector: 'p' })
    ).toBeTruthy()
    // Screen readers hear the same state as the one shown.
    const summary = screen.getByRole('region', { name: 'summary.title' })
    expect(within(summary).getByRole('status').textContent).toBe(
      'summary.requestFailed.title'
    )
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(calls(SUMMARY_URL, 'POST')).toBe(1)
  })

  it('re-reads the detail when the transcript is not ready yet (409)', async () => {
    serve({
      detail: () => detail('absent', 'available'),
      summary: () => {
        throw new ApiError(409, {})
      },
    })
    renderDetail()

    await waitFor(() => expect(calls(DETAIL_URL)).toBe(2))
    expect(calls(SUMMARY_URL, 'POST')).toBe(1)
    expect(screen.queryByText('summary.requestFailed.title')).toBeNull()
  })

  it('shows an available summary as automatic and unreviewed', async () => {
    serve({ detail: () => detail('available', 'available') })
    renderDetail()

    expect(await screen.findByText('Résumé prêt.')).toBeTruthy()
    expect(screen.getByText('summary.automatic')).toBeTruthy()
    expect(calls(SUMMARY_URL, 'POST')).toBe(0)
  })
})

describe('processing refresh', () => {
  it('polls only while something is processing', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date('2026-09-20T09:00:00Z'))
    let summaryStatus = 'processing'
    serve({ detail: () => detail(summaryStatus, 'available') })
    renderDetail()
    await screen.findByText('summary.transcribing.title')
    expect(calls(DETAIL_URL)).toBe(1)

    summaryStatus = 'available'
    await vi.advanceTimersByTimeAsync(15_000)
    expect(await screen.findByText('Résumé prêt.')).toBeTruthy()
    expect(calls(DETAIL_URL)).toBe(2)

    await vi.advanceTimersByTimeAsync(45_000)
    expect(calls(DETAIL_URL)).toBe(2)
  })
})
