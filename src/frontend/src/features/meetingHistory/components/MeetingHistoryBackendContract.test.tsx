import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import {
  fetchMeetingHistoryDetail,
  fetchMeetingHistoryPage,
  requestMeetingSummary,
} from '../api/meetingHistoryApi'
import { redirectToLogin } from '../api/loginRedirect'
import { MeetingHistoryDetailView } from './MeetingHistoryDetailView'
import { MeetingHistoryList } from './MeetingHistoryList'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'fr', resolvedLanguage: 'fr' },
  }),
}))
vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
vi.mock('../api/loginRedirect', () => ({ redirectToLogin: vi.fn() }))
vi.mock('wouter', () => ({
  Link: ({ children }: { children: React.ReactNode }) => (
    <a href="#">{children}</a>
  ),
}))
vi.mock('@/features/home/components/CreateMeetingMenu', () => ({
  CreateMeetingMenu: () => null,
}))

const fetchApiMock = vi.mocked(fetchApi)

// Payloads shaped exactly like the Platform facade contract (zod schemas).
const platformItem = {
  id: 'meeting_01',
  title: null,
  started_at: '2026-09-27T10:00:00.000Z',
  ended_at: '2026-09-27T10:32:00.000Z',
  participant_count: null,
  summary_status: 'absent',
  transcript_status: 'available',
}
const platformDetail = {
  ...platformItem,
  summary: null,
  transcript: {
    status: 'available',
    segments: [
      { id: 'segment_1', start_ms: 1200, speaker: 'Matthias', text: 'Bonjour' },
    ],
    truncated: false,
  },
}

const renderWithClient = (ui: React.ReactNode) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } })
      }
    >
      {ui}
    </QueryClientProvider>
  )

beforeEach(() => {
  fetchApiMock.mockReset()
  vi.mocked(redirectToLogin).mockReset()
})
afterEach(cleanup)

describe('Meet history backend contract', () => {
  it('reads the list, detail and summary trigger payloads', async () => {
    fetchApiMock.mockResolvedValueOnce({
      results: [platformItem],
      next_cursor: null,
    })
    const page = await fetchMeetingHistoryPage(null)
    expect(fetchApiMock).toHaveBeenLastCalledWith('meetings/history/')
    expect(page.items[0]).toMatchObject({
      id: 'meeting_01',
      title: null,
      participantCount: null,
      summaryStatus: 'absent',
      transcriptStatus: 'available',
    })

    fetchApiMock.mockResolvedValueOnce({ results: [], next_cursor: null })
    await fetchMeetingHistoryPage('abc_123')
    expect(fetchApiMock).toHaveBeenLastCalledWith(
      'meetings/history/?cursor=abc_123'
    )

    fetchApiMock.mockResolvedValueOnce(platformDetail)
    const detail = await fetchMeetingHistoryDetail('meeting_01')
    expect(fetchApiMock).toHaveBeenLastCalledWith(
      'meetings/history/meeting_01/'
    )
    expect(detail.summary.status).toBe('absent')
    expect(detail.transcript.segments[0]).toMatchObject({
      speaker: 'Matthias',
      startMs: 1200,
    })

    fetchApiMock.mockResolvedValueOnce({ summary_status: 'processing' })
    await expect(requestMeetingSummary('meeting_01')).resolves.toBe(
      'processing'
    )
    expect(fetchApiMock).toHaveBeenLastCalledWith(
      'meetings/history/meeting_01/summary/',
      { method: 'POST' }
    )
  })

  it('sends a 401 on the list to the existing login flow', async () => {
    fetchApiMock.mockRejectedValue(new ApiError(401, {}))
    renderWithClient(<MeetingHistoryList />)
    await waitFor(() => expect(redirectToLogin).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('error.title')).toBeNull()
    expect(fetchApiMock).toHaveBeenCalledTimes(1)
  })

  it('sends a 401 on the detail to the existing login flow', async () => {
    fetchApiMock.mockRejectedValue(new ApiError(401, {}))
    renderWithClient(<MeetingHistoryDetailView meetingId="meeting_01" />)
    await waitFor(() => expect(redirectToLogin).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('detail.notFound.title')).toBeNull()
  })

  it('offers a manual retry after a 503 on the summary request', async () => {
    let attempts = 0
    fetchApiMock.mockImplementation(
      async (_url: string, options?: RequestInit) => {
        if (options?.method === 'POST') {
          attempts += 1
          if (attempts === 1) throw new ApiError(503, {})
          return { summary_status: 'processing' }
        }
        return platformDetail
      }
    )
    renderWithClient(<MeetingHistoryDetailView meetingId="meeting_01" />)

    fireEvent.click(await screen.findByRole('button', { name: 'error.retry' }))
    expect(await screen.findByText('summary.processing.title')).toBeTruthy()
    expect(attempts).toBe(2)
  })
})
