import { useEffect, useRef, useState } from 'react'
import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { ApiError } from '@/api/ApiError'
import { keys } from '@/api/queryKeys'
import { useLoginRedirectOnAuthError } from '@/features/meetingHistory/api/authRedirect'
import {
  classifyMeeting,
  createMatter,
  findMatters,
  readClassification,
  type ClassifyCommand,
  type CreateMatterCommand,
} from './classificationApi'
import type {
  DestinationSearch,
  MatterDestination,
} from './ClassificationDialog'

export function useMeetingClassification(
  meetingId: string,
  identityKey?: string
) {
  const client = useQueryClient()
  const [isOpen, setOpen] = useState(false)
  const [searchText, setSearchText] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<'conflict' | 'failed' | null>(null)
  const [createdMatter, setCreatedMatter] = useState<MatterDestination | null>(
    null
  )
  const [knownMatter, setKnownMatter] = useState<MatterDestination | null>(null)
  const active = useRef(true)
  const controller = useRef<AbortController | null>(null)
  const busy = useRef(false)
  const classificationCommand = useRef<ClassifyCommand | null>(null)
  const creationCommand = useRef<CreateMatterCommand | null>(null)
  const queryKey = ['meeting-classification', identityKey, meetingId]
  const current = useQuery({
    queryKey,
    queryFn: ({ signal }) => readClassification(meetingId, signal),
    enabled: Boolean(identityKey),
    retry: false,
    staleTime: 0,
    gcTime: 0,
  })
  const destinations = useInfiniteQuery({
    queryKey: ['meeting-destinations', identityKey, meetingId, searchText],
    queryFn: ({ pageParam, signal }) =>
      findMatters(searchText, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    enabled: isOpen && current.data?.can_classify === true,
    retry: false,
    staleTime: 0,
    gcTime: 0,
  })
  useLoginRedirectOnAuthError(current.error)
  useEffect(() => {
    active.current = true
    return () => {
      active.current = false
      controller.current?.abort()
    }
  }, [])

  async function submit(matter: MatterDestination | null, label?: string) {
    const classification = current.data
    if (busy.current || current.isError || !classification?.can_classify) return
    busy.current = true
    setPending(true)
    setError(null)
    const request = new AbortController()
    controller.current = request
    try {
      let target = createdMatter ?? matter
      if (!target && label) {
        if (creationCommand.current?.display_label !== label)
          creationCommand.current = {
            version: 1,
            command_id: crypto.randomUUID(),
            display_label: label,
          }
        target = await createMatter(creationCommand.current, request.signal)
        if (!active.current) return
        setCreatedMatter(target)
      }
      if (!target) return
      const previous = classificationCommand.current
      if (
        !previous ||
        previous.matter_ref !== target.matter_ref ||
        previous.expected_classification_version !==
          classification.classification_version
      ) {
        classificationCommand.current = {
          version: 1,
          meeting_ref: meetingId,
          matter_ref: target.matter_ref,
          expected_classification_version:
            classification.classification_version,
          command_id: crypto.randomUUID(),
        }
      }
      const command = classificationCommand.current
      if (!command) return
      const result = await classifyMeeting(command, request.signal)
      if (!active.current) return
      client.setQueryData(queryKey, result)
      setKnownMatter(target)
      setCreatedMatter(null)
      setOpen(false)
      classificationCommand.current = null
      creationCommand.current = null
      void client.invalidateQueries({ queryKey: [keys.meetingHistory] })
    } catch (failure) {
      if (!active.current) return
      if (failure instanceof ApiError && failure.statusCode === 409) {
        await current.refetch()
        if (active.current) setError('conflict')
      } else {
        setError('failed')
        if (
          failure instanceof ApiError &&
          [401, 403, 404].includes(failure.statusCode)
        )
          await current.refetch()
      }
    } finally {
      busy.current = false
      if (active.current) setPending(false)
    }
  }
  let phase: DestinationSearch['phase'] = 'ready'
  if (destinations.isPending || destinations.isFetching) phase = 'loading'
  else if (destinations.isError) phase = 'error'
  const search: DestinationSearch = {
    phase,
    items: destinations.data?.pages.flatMap((page) => page.items) ?? [],
    hasMore: destinations.hasNextPage,
  }
  const currentMatter =
    knownMatter?.matter_ref === current.data?.matter_ref ? knownMatter : null
  return {
    current,
    currentMatter,
    search,
    pending,
    error,
    createdMatter,
    isOpen,
    open: () => setOpen(true),
    close: () => {
      if (!busy.current) setOpen(false)
    },
    onSearch: (text: string) => {
      setSearchText(text)
      if (text === searchText) void destinations.refetch()
    },
    onLoadMore: () => {
      if (!destinations.isFetching) void destinations.fetchNextPage()
    },
    onClassify: (matter: MatterDestination) => void submit(matter),
    onCreate: (label: string) => void submit(null, label),
  }
}
