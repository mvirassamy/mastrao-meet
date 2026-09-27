import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { ApiError } from '@/api/ApiError'
import { keys } from '@/api/queryKeys'
import {
  fetchMeetingHistoryDetail,
  fetchMeetingHistoryPage,
  requestMeetingSummary,
} from './meetingHistoryApi'
import type { MeetingContentStatus, MeetingHistoryDetail } from './types'
import { isAuthRequiredError } from './authRedirect'
import { redirectToLogin } from './loginRedirect'

const PROCESSING_REFRESH_MS = 15_000

const listKey = [keys.meetingHistory, 'list'] as const
const detailKey = (meetingId: string) =>
  [keys.meetingHistory, 'detail', meetingId] as const

export const isMeetingAccessError = (error: unknown) =>
  error instanceof ApiError && [401, 403, 404].includes(error.statusCode)

const retryUnlessAccessError = (failureCount: number, error: unknown) =>
  !isMeetingAccessError(error) && failureCount < 1

const hasProcessing = (...statuses: MeetingContentStatus[]) =>
  statuses.includes('processing')

export const useMeetingHistory = () =>
  useInfiniteQuery({
    queryKey: listKey,
    queryFn: ({ pageParam }) => fetchMeetingHistoryPage(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
    retry: retryUnlessAccessError,
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) =>
        page.items.some((item) =>
          hasProcessing(item.summaryStatus, item.transcriptStatus)
        )
      )
        ? PROCESSING_REFRESH_MS
        : false,
  })

export const useMeetingHistoryDetail = (meetingId: string) =>
  useQuery({
    queryKey: detailKey(meetingId),
    queryFn: () => fetchMeetingHistoryDetail(meetingId),
    retry: retryUnlessAccessError,
    refetchInterval: (query) => {
      const data = query.state.data
      return data && hasProcessing(data.summaryStatus, data.transcriptStatus)
        ? PROCESSING_REFRESH_MS
        : false
    },
  })

/**
 * Starts the automatic summary. A "processing" answer is written into the
 * cached detail right away, which switches the page to its 15 s polling.
 */
export const useRequestMeetingSummary = (meetingId: string) => {
  const queryClient = useQueryClient()
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: detailKey(meetingId) })
    void queryClient.invalidateQueries({ queryKey: listKey })
  }

  return useMutation({
    mutationFn: () => requestMeetingSummary(meetingId),
    onSuccess: (status) => {
      if (status === 'processing') {
        queryClient.setQueryData<MeetingHistoryDetail>(
          detailKey(meetingId),
          (current) =>
            current && {
              ...current,
              summaryStatus: 'processing',
              summary: { status: 'processing', paragraphs: [], sections: [] },
            }
        )
        void queryClient.invalidateQueries({ queryKey: listKey })
        return
      }
      refresh()
    },
    onError: (error) => {
      if (isAuthRequiredError(error)) {
        redirectToLogin()
        return
      }
      // 409: transcript not ready yet; 404: meeting gone. Re-read the truth.
      if (error instanceof ApiError && [404, 409].includes(error.statusCode))
        refresh()
    },
  })
}

export const shouldRequestSummary = (detail: MeetingHistoryDetail) =>
  detail.transcript.status === 'available' && detail.summary.status === 'absent'
