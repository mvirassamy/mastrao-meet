import { useEffect, useMemo } from 'react'
import { dayKey } from '../utils/meetingHistoryFormat'
import {
  isAuthRequiredError,
  useLoginRedirectOnAuthError,
} from './authRedirect'
import { historyItemsOf } from './historyItems'
import type { MeetingHistoryItem } from './types'
import { useMeetingHistory } from './useMeetingHistory'

/** Meetings that started on `day` (YYYY-MM-DD) in the user's time zone. */
export const meetingsOfDay = (
  items: MeetingHistoryItem[],
  day: string,
  timeZone?: string
) => items.filter((item) => dayKey(item.startedAt, timeZone) === day)

/**
 * History pages come most recent first: an older page can still hold
 * meetings of `day` as long as the oldest loaded one is not before it.
 */
export const olderPagesMayHoldDay = (
  items: MeetingHistoryItem[],
  day: string,
  timeZone?: string
) => {
  const oldest = items[items.length - 1]
  return !oldest || dayKey(oldest.startedAt, timeZone) >= day
}

export type MeetingsOfDayState =
  | { status: 'loading' }
  | { status: 'error'; retry: () => void }
  | { status: 'ready'; items: MeetingHistoryItem[] }

/**
 * Past meetings of one calendar day, read from the meeting history. Older
 * history pages are loaded until the day is fully covered.
 */
export const useMeetingsOfDay = (
  day: string,
  timeZone?: string
): MeetingsOfDayState => {
  const query = useMeetingHistory()
  useLoginRedirectOnAuthError(query.error)
  const items = useMemo(() => historyItemsOf(query.data?.pages), [query.data])

  const { fetchNextPage, isFetchingNextPage, isFetchNextPageError } = query
  const needsOlderPage =
    query.hasNextPage && olderPagesMayHoldDay(items, day, timeZone)
  useEffect(() => {
    if (needsOlderPage && !isFetchingNextPage && !isFetchNextPageError)
      void fetchNextPage()
  }, [needsOlderPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage])

  if (query.isPending || isAuthRequiredError(query.error))
    return { status: 'loading' }
  if (!query.data) return { status: 'error', retry: () => void query.refetch() }
  if (needsOlderPage && isFetchNextPageError)
    return { status: 'error', retry: () => void fetchNextPage() }
  if (needsOlderPage) return { status: 'loading' }
  return { status: 'ready', items: meetingsOfDay(items, day, timeZone) }
}
