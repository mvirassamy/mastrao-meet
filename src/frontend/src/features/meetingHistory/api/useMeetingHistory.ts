import {
  type InfiniteData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { ApiError } from '@/api/ApiError'
import { keys } from '@/api/queryKeys'
import {
  fetchMeetingHistoryDetail,
  fetchMeetingHistoryPage,
  mergeHistoryDetail,
  mergeHistoryItem,
  requestMeetingSummary,
} from './meetingHistoryApi'
import type {
  MeetingContentStatus,
  MeetingRecordingStatus,
  MeetingHistoryDetail,
  MeetingHistoryPage,
} from './types'
import { isAuthRequiredError } from './authRedirect'
import { redirectToLogin } from './loginRedirect'

const PROCESSING_REFRESH_DELAYS_MS = [5_000, 15_000, 30_000, 60_000] as const
const PROCESSING_MAX_AGE_MS = 24 * 60 * 60 * 1_000

export const adaptiveProcessingRefreshMs = (
  updateCount: number,
  seed: number
) => {
  const base =
    PROCESSING_REFRESH_DELAYS_MS[
      Math.min(
        Math.max(updateCount, 0),
        PROCESSING_REFRESH_DELAYS_MS.length - 1
      )
    ] ?? 60_000
  const jitter = ((Math.abs(seed) % 2_001) - 1_000) / 10_000
  return Math.round(base * (1 + jitter))
}

const listKey = [keys.meetingHistory, 'list'] as const
const detailKey = (meetingId: string) =>
  [keys.meetingHistory, 'detail', meetingId] as const

export const isMeetingAccessError = (error: unknown) =>
  error instanceof ApiError && [401, 403, 404].includes(error.statusCode)

const retryUnlessAccessError = (failureCount: number, error: unknown) =>
  !isMeetingAccessError(error) && failureCount < 1

const hasProcessing = (...statuses: MeetingContentStatus[]) =>
  statuses.some((status) =>
    ['unknown', 'waiting_for_audio', 'transcribing'].includes(status)
  )

const isReadable = (status: MeetingContentStatus) =>
  status === 'available' || status === 'partial'

export const shouldPollForContent = (
  summaryStatus: MeetingContentStatus,
  transcriptStatus: MeetingContentStatus,
  summaryRefreshPending = false,
  transcriptRefreshPending = false,
  recordingStatus: MeetingRecordingStatus = 'absent'
) =>
  summaryRefreshPending ||
  transcriptRefreshPending ||
  recordingStatus === 'processing' ||
  recordingStatus === 'unknown' ||
  hasProcessing(summaryStatus, transcriptStatus) ||
  (summaryStatus === 'not_started' && isReadable(transcriptStatus))

export const isWithinPollingDeadline = (deadline: number, now = Date.now()) =>
  now <= deadline

export const shouldPollItem = (item: {
  startedAt: Date
  endedAt: Date | null
  summaryStatus: MeetingContentStatus
  transcriptStatus: MeetingContentStatus
  recordingStatus?: MeetingRecordingStatus
  summaryProjection: { refreshPending?: boolean }
  transcriptProjection: { refreshPending?: boolean }
}) =>
  Date.now() - item.startedAt.getTime() <= PROCESSING_MAX_AGE_MS &&
  shouldPollForContent(
    item.summaryStatus,
    item.transcriptStatus,
    item.summaryProjection.refreshPending === true,
    item.transcriptProjection.refreshPending === true,
    item.recordingStatus
  )

export const useMeetingHistory = () =>
  useInfiniteQuery({
    queryKey: listKey,
    queryFn: ({ pageParam }) => fetchMeetingHistoryPage(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
    retry: retryUnlessAccessError,
    structuralSharing: (current, incoming) => {
      const currentData = current as
        | InfiniteData<MeetingHistoryPage, string | null>
        | undefined
      const incomingData = incoming as InfiniteData<
        MeetingHistoryPage,
        string | null
      >
      if (!currentData) return incomingData
      const previousItems = new Map(
        currentData.pages.flatMap((page) =>
          page.items.map((item) => [item.id, item] as const)
        )
      )
      return {
        ...incomingData,
        pages: incomingData.pages.map((page) => ({
          ...page,
          items: page.items.map((item) => {
            const previous = previousItems.get(item.id)
            return previous ? mergeHistoryItem(previous, item) : item
          }),
        })),
      }
    },
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) =>
        page.items.some((item) => shouldPollItem(item))
      )
        ? adaptiveProcessingRefreshMs(
            Math.max((query.state.dataUpdateCount ?? 1) - 1, 0),
            query.state.dataUpdatedAt
          )
        : false,
  })

export const useMeetingHistoryDetail = (meetingId: string) => {
  const [pollingDeadline, setPollingDeadline] = useState(
    Number.POSITIVE_INFINITY
  )
  useEffect(() => {
    setPollingDeadline(Date.now() + PROCESSING_MAX_AGE_MS)
  }, [meetingId])

  return useQuery({
    queryKey: detailKey(meetingId),
    queryFn: () => fetchMeetingHistoryDetail(meetingId),
    retry: retryUnlessAccessError,
    structuralSharing: (current, incoming) => {
      const currentDetail = current as MeetingHistoryDetail | undefined
      const incomingDetail = incoming as MeetingHistoryDetail
      return currentDetail
        ? mergeHistoryDetail(currentDetail, incomingDetail)
        : incomingDetail
    },
    refetchInterval: (query) => {
      const data = query.state.data
      const updateCount = query.state.dataUpdateCount ?? 0
      return data &&
        isWithinPollingDeadline(pollingDeadline) &&
        shouldPollForContent(
          data.summaryStatus,
          data.transcriptStatus,
          data.summaryProjection.refreshPending === true,
          data.transcriptProjection.refreshPending === true,
          data.recordingStatus
        )
        ? adaptiveProcessingRefreshMs(
            Math.max(updateCount - 1, 0),
            query.state.dataUpdatedAt
          )
        : false
    },
  })
}

/**
 * Starts the automatic summary. A "processing" answer is written into the
 * cached detail right away, which switches the page to adaptive polling.
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
      if (status === 'transcribing') {
        queryClient.setQueryData<MeetingHistoryDetail>(
          detailKey(meetingId),
          (current) =>
            current && {
              ...current,
              summaryStatus: 'transcribing',
              summaryProjection: {
                ...current.summaryProjection,
                state: 'transcribing',
                digest: null,
                source: null,
              },
              summary: {
                status: 'transcribing',
                projection: {
                  ...current.summaryProjection,
                  state: 'transcribing',
                  digest: null,
                  source: null,
                },
                paragraphs: [],
                sections: [],
              },
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
  ['available', 'partial'].includes(detail.transcript.status) &&
  detail.summary.status === 'not_started'
