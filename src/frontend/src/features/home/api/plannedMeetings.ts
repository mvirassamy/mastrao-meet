import { useEffect } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import { isMastraoRoomId } from '@/features/rooms/utils/isRoomValid'
import { useLoginRedirectOnAuthError } from '@/features/meetingHistory/api/authRedirect'
import { meetingDayBounds } from '../utils/meetingDayBounds'

export type PlannedMeeting = {
  id: string
  roomRef: string
  title: string | null
  startsAt: Date
  endsAt: Date
  isClosed: boolean
}
export type PlannedMeetingsPage = {
  items: PlannedMeeting[]
  nextCursor: string | null
}
export const plannedMeetingsQueryKey = ['plannedMeetings'] as const
const states = ['admitted', 'ready', 'cancelled', 'ending', 'ended']
const reference = /^[A-Za-z0-9_-]{16,160}$/
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const isTimestamp = (value: unknown): value is number =>
  typeof value === 'number' &&
  Number.isSafeInteger(value) &&
  value >= 0 &&
  Number.isFinite(new Date(value * 1000).getTime())
export class PlannedMeetingsContractError extends Error {
  constructor() {
    super('Unexpected planned meetings response')
  }
}

export const normalizePlannedMeetingsPage = (
  raw: unknown
): PlannedMeetingsPage => {
  if (
    !isRecord(raw) ||
    !Array.isArray(raw.results) ||
    !(raw.next_cursor === null || typeof raw.next_cursor === 'string')
  )
    throw new PlannedMeetingsContractError()
  const items: PlannedMeeting[] = []
  for (const item of raw.results) {
    if (!isRecord(item)) throw new PlannedMeetingsContractError()
    if (
      typeof item.meeting_ref !== 'string' ||
      !reference.test(item.meeting_ref) ||
      typeof item.room_ref !== 'string' ||
      !isMastraoRoomId(item.room_ref) ||
      !(item.title === null || typeof item.title === 'string') ||
      !isTimestamp(item.scheduled_start_at) ||
      !isTimestamp(item.scheduled_end_at) ||
      item.scheduled_end_at <= item.scheduled_start_at ||
      typeof item.timezone !== 'string' ||
      typeof item.state !== 'string' ||
      !states.includes(item.state)
    )
      throw new PlannedMeetingsContractError()
    items.push({
      id: item.meeting_ref,
      roomRef: item.room_ref,
      title: item.title,
      startsAt: new Date(item.scheduled_start_at * 1000),
      endsAt: new Date(item.scheduled_end_at * 1000),
      isClosed: !['admitted', 'ready'].includes(item.state),
    })
  }
  return { items, nextCursor: raw.next_cursor }
}

export const fetchPlannedMeetingsPage = async (
  day: string,
  timeZone?: string,
  cursor: string | null = null
) => {
  const bounds = meetingDayBounds(day, timeZone)
  const params = new URLSearchParams({
    day_start: String(bounds.start),
    day_end: String(bounds.end),
  })
  if (cursor !== null) params.set('cursor', cursor)
  return normalizePlannedMeetingsPage(await fetchApi(`meetings/?${params}`))
}

/** Canonical day filtering occurs before server pagination; load every page of that day. */
export const usePlannedMeetingsOfDay = (day: string, timeZone?: string) => {
  const query = useInfiniteQuery({
    queryKey: [...plannedMeetingsQueryKey, day, timeZone],
    queryFn: ({ pageParam }) =>
      fetchPlannedMeetingsPage(day, timeZone, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.nextCursor,
    retry: (count, error) =>
      !(
        error instanceof ApiError && [401, 403, 404].includes(error.statusCode)
      ) && count < 1,
  })
  useLoginRedirectOnAuthError(query.error)
  const {
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
  } = query
  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage && !isFetchNextPageError)
      void fetchNextPage()
  }, [hasNextPage, fetchNextPage, isFetchingNextPage, isFetchNextPageError])
  const seen = new Set<string>()
  const items = (query.data?.pages ?? [])
    .flatMap((page) => page.items)
    .filter((item) => {
      if (seen.has(item.id)) return false
      seen.add(item.id)
      return true
    })
  return { ...query, items }
}
