import {
  cleanup,
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import { keys } from '@/api/queryKeys'
import { logout } from '@/features/auth/utils/logout'
import {
  fetchMeetingHistoryDetail,
  fetchMeetingHistoryPage,
  requestMeetingSummary,
} from '../api/meetingHistoryApi'
import { redirectToLogin } from '../api/loginRedirect'
import { MeetingHistoryDetailView } from './MeetingHistoryDetailView'
import { MeetingHistoryList } from './MeetingHistoryList'
import {
  fullDigest,
  fullHistoryDetail,
  fullMeetingRef,
  fullResponse,
} from '../api/meetingNativeFull.fixtures'
import {
  liveDetail,
  liveMeetingRef,
} from '../api/meetingLiveTranscript.fixtures'
import {
  nativeDetail,
  nativeMeetingRef,
} from '../api/meetingNativePartial.fixtures'

import {
  liveFullDetail,
  liveFullResponse,
  liveHeadsDigest,
} from '../api/meetingLiveFull.fixtures'

// Wide screen: the assistant sits beside the page.
vi.mock('@/features/rooms/livekit/hooks/useMediaQuery', () => ({
  useMediaQuery: () => true,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: { email?: string }) =>
      values?.email ? `${key}:${values.email}` : key,
    i18n: { language: 'fr', resolvedLanguage: 'fr' },
  }),
}))
vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
vi.mock('../api/loginRedirect', () => ({ redirectToLogin: vi.fn() }))
vi.mock('@/features/auth/utils/logout', () => ({ logout: vi.fn() }))
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
  it.each([
    { kind: 'native', phase: 'loaded' },
    { kind: 'native', phase: 'in_flight' },
    { kind: 'live', phase: 'loaded' },
    { kind: 'live', phase: 'in_flight' },
  ] as const)(
    'withdraws $phase $kind consultation after an invalid whole history and requires a new explicit read',
    async ({ kind, phase }) => {
      const history = kind === 'live' ? liveFullDetail : fullHistoryDetail
      const response = kind === 'live' ? liveFullResponse : fullResponse
      const meetingRef = kind === 'live' ? liveMeetingRef : fullMeetingRef
      const loadLabel = `transcript.${kind}.full.load`
      const titleLabel = `transcript.${kind}.full.title`
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retryDelay: 0 } },
      })
      const queryKey = [keys.meetingHistory, 'detail', meetingRef]
      let invalidHistory = false
      let resolvePending: ((value: unknown) => void) | undefined
      const pending = new Promise<unknown>((resolve) => {
        resolvePending = resolve
      })
      let consultationReads = 0
      let originalSignal: AbortSignal | undefined
      fetchApiMock.mockImplementation(async (url, options) => {
        if (url.includes(`/${kind}-transcript/`)) {
          consultationReads++
          originalSignal ??= options?.signal as AbortSignal | undefined
          if (phase === 'in_flight' && consultationReads === 1) return pending
          return response()
        }
        if (invalidHistory) return null
        return history()
      })
      render(
        <QueryClientProvider client={queryClient}>
          <MeetingHistoryDetailView meetingId={meetingRef} />
        </QueryClientProvider>
      )
      fireEvent.click(
        await screen.findByRole('button', {
          name: loadLabel,
        })
      )
      if (phase === 'loaded')
        await screen.findByRole('region', {
          name: titleLabel,
        })
      else await waitFor(() => expect(consultationReads).toBe(1))
      const canonicalBefore = queryClient.getQueryData(queryKey)
      invalidHistory = true
      await act(async () => {
        await queryClient.invalidateQueries({ queryKey })
      })
      await waitFor(() =>
        expect(queryClient.getQueryState(queryKey)?.status).toBe('error')
      )
      expect(screen.queryByRole('region', { name: titleLabel })).toBeNull()
      expect(screen.queryByRole('button', { name: loadLabel })).toBeNull()
      if (phase === 'in_flight') expect(originalSignal?.aborted).toBe(true)
      expect(queryClient.getQueryData(queryKey)).toBe(canonicalBefore)
      invalidHistory = false
      await act(async () => {
        await queryClient.invalidateQueries({ queryKey })
      })
      await screen.findByRole('button', { name: loadLabel })
      await act(async () => {
        resolvePending?.(response())
        await pending
      })
      expect(screen.queryByRole('region', { name: titleLabel })).toBeNull()
      expect(consultationReads).toBe(1)
      fireEvent.click(screen.getByRole('button', { name: loadLabel }))
      await screen.findByRole('region', {
        name: titleLabel,
      })
      expect(consultationReads).toBe(2)
      queryClient.clear()
    }
  )

  it.each([
    { kind: 'native', status: 401 },
    { kind: 'native', status: 403 },
    { kind: 'native', status: 404 },
    { kind: 'live', status: 401 },
    { kind: 'live', status: 403 },
    { kind: 'live', status: 404 },
  ] as const)(
    'binds $kind consultation to the detail meeting and withdraws it on access refusal ($status)',
    async ({ kind, status }) => {
      const history = kind === 'live' ? liveFullDetail : fullHistoryDetail
      const response = kind === 'live' ? liveFullResponse : fullResponse
      const meetingRef = kind === 'live' ? liveMeetingRef : fullMeetingRef
      const digest = kind === 'live' ? liveHeadsDigest : fullDigest
      const digestField =
        kind === 'live' ? 'source_heads_digest' : 'snapshot_digest'
      const loadLabel = `transcript.${kind}.full.load`
      const titleLabel = `transcript.${kind}.full.title`
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retryDelay: 0 } },
      })
      const queryKey = [keys.meetingHistory, 'detail', meetingRef]
      fetchApiMock.mockImplementation(async (url) => {
        if (url.includes(`/${kind}-transcript/`)) return response()
        return history()
      })
      render(
        <QueryClientProvider client={queryClient}>
          <MeetingHistoryDetailView meetingId={meetingRef} />
        </QueryClientProvider>
      )
      fireEvent.click(
        await screen.findByRole('button', {
          name: loadLabel,
        })
      )
      expect(
        await screen.findByRole('region', {
          name: titleLabel,
        })
      ).toBeTruthy()
      expect(fetchApiMock).toHaveBeenCalledWith(
        `meetings/history/${meetingRef}/${kind}-transcript/?${digestField}=${digest}`,
        { cache: 'no-store', signal: expect.any(AbortSignal) }
      )
      expect(
        fetchApiMock.mock.calls.filter(([url]) =>
          url.includes(`/${kind}-transcript/`)
        )
      ).toHaveLength(1)
      expect(
        fetchApiMock.mock.calls.some(
          ([, options]) => options?.method === 'POST'
        )
      ).toBe(false)

      fetchApiMock.mockRejectedValue(new ApiError(status, {}))
      await act(async () => {
        await queryClient.invalidateQueries({ queryKey })
      })
      if (status === 401)
        await waitFor(() => expect(redirectToLogin).toHaveBeenCalled())
      else expect(await screen.findByText('detail.notFound.title')).toBeTruthy()
      expect(
        screen.queryByRole('region', {
          name: titleLabel,
        })
      ).toBeNull()
      expect(screen.queryByText(loadLabel)).toBeNull()
      queryClient.clear()
    }
  )

  it.each(
    [401, 403, 404].flatMap((status) => [
      {
        kind: 'live',
        status,
        meetingRef: liveMeetingRef,
        response: liveDetail(),
        withdrawn: liveDetail({ live_transcript: null }),
        finalState: 'failed',
      },
      {
        kind: 'native partial',
        status,
        meetingRef: nativeMeetingRef,
        response: nativeDetail(),
        withdrawn: nativeDetail({ native_partial: null }),
        finalState: 'transcribing',
      },
    ])
  )(
    'hides $kind content after access revocation ($status) and does not resurrect it',
    async ({ status, meetingRef, response, withdrawn, finalState }) => {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retryDelay: 0 } },
      })
      const queryKey = [keys.meetingHistory, 'detail', meetingRef]
      fetchApiMock.mockResolvedValue(response)
      render(
        <QueryClientProvider client={queryClient}>
          <MeetingHistoryDetailView meetingId={meetingRef} />
        </QueryClientProvider>
      )
      const exactText = '  De\u0301cision : ne pas payer 250 €.\n'
      expect(
        await screen.findByText(
          (_, element) =>
            element?.tagName === 'P' && element.textContent === exactText
        )
      ).toBeTruthy()

      fetchApiMock.mockRejectedValue(new ApiError(status, {}))
      await act(async () => {
        await queryClient.invalidateQueries({ queryKey })
      })
      if (status === 401)
        await waitFor(() => expect(redirectToLogin).toHaveBeenCalled())
      else expect(await screen.findByText('detail.notFound.title')).toBeTruthy()
      expect(
        screen.queryByText(
          (_, element) =>
            element?.tagName === 'P' && element.textContent === exactText
        )
      ).toBeNull()

      fetchApiMock.mockResolvedValue(withdrawn)
      await act(async () => {
        await queryClient.invalidateQueries({ queryKey })
      })
      expect(
        await screen.findByText(`transcript.${finalState}.title`)
      ).toBeTruthy()
      expect(
        screen.queryByText(
          (_, element) =>
            element?.tagName === 'P' && element.textContent === exactText
        )
      ).toBeNull()
      queryClient.clear()
    }
  )

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
      summaryStatus: 'not_started',
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
    expect(detail.summary.status).toBe('not_started')
    expect(detail.transcript.segments[0]).toMatchObject({
      speaker: 'Matthias',
      startMs: 1200,
    })

    fetchApiMock.mockResolvedValueOnce({ summary_status: 'processing' })
    await expect(requestMeetingSummary('meeting_01')).resolves.toBe(
      'transcribing'
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

  it('identifies the active account when a meeting is inaccessible', async () => {
    fetchApiMock.mockRejectedValue(new ApiError(404, {}))
    renderWithClient(
      <MeetingHistoryDetailView
        meetingId="meeting_01"
        accountEmail="viewer@example.test"
      />
    )

    expect(
      await screen.findByText(
        'detail.notFound.descriptionWithAccount:viewer@example.test'
      )
    ).toBeTruthy()
    fireEvent.click(
      screen.getByRole('button', { name: 'detail.notFound.switch' })
    )
    expect(logout).toHaveBeenCalledTimes(1)
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
    expect(await screen.findByText('summary.transcribing.title')).toBeTruthy()
    expect(attempts).toBe(2)
  })
})
