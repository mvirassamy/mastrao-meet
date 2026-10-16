import { useEffect, useRef, useState } from 'react'
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '@/api/ApiError'
import { useLoginRedirectOnAuthError } from '@/features/meetingHistory/api/authRedirect'
import {
  readAssistantThread,
  sendAssistantCommand,
  deleteAssistantThread,
} from './assistantApi'
import type {
  MeetingAssistantDeleteRequest,
  MeetingAssistantOperation,
  MeetingAssistantSendRequest,
  MeetingAssistantThread,
} from './types'

export function useMeetingAssistant(meetingId: string, identityKey: string) {
  const client = useQueryClient()
  const queryKey = ['meeting-assistant', identityKey, meetingId]
  const query = useInfiniteQuery({
    queryKey,
    queryFn: ({ signal, pageParam }) =>
      readAssistantThread(meetingId, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    retry: false,
    staleTime: 0,
    gcTime: 0,
  })
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<
    'unconfirmed' | 'conflict' | 'rejected' | null
  >(null)
  const [retryCommand, setRetryCommand] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const active = useRef(true)
  const busy = useRef(false)
  const request = useRef<AbortController | null>(null)
  const sendCommand = useRef<MeetingAssistantSendRequest | null>(null)
  const deleteCommand = useRef<MeetingAssistantDeleteRequest | null>(null)
  useLoginRedirectOnAuthError(query.error)
  useEffect(() => {
    active.current = true
    return () => {
      active.current = false
      request.current?.abort()
    }
  }, [])

  const thread = query.data?.pages[0]
  const turns = [...(query.data?.pages ?? [])]
    .reverse()
    .flatMap((page) => page.turns)
  const unresolved = turns.some(
    (turn) => turn.state === 'processing' || turn.state === 'unknown'
  )
  const canDelete =
    !pending &&
    !query.isFetching &&
    !query.isError &&
    Boolean(thread) &&
    !sendCommand.current &&
    !deleteCommand.current
  const canSend = canDelete && !unresolved

  function replaceThread(value: MeetingAssistantThread) {
    client.setQueryData(queryKey, { pages: [value], pageParams: [null] })
  }
  async function refresh() {
    if (busy.current) return
    setRetryCommand(false)
    const result = await query.refetch()
    if (!active.current || result.isError) return
    const pages = result.data?.pages ?? []
    if (
      deleteCommand.current &&
      pages[0]?.deleted &&
      pages[0].thread_version >= deleteCommand.current.expected_thread_version
    ) {
      deleteCommand.current = null
      setConfirmDelete(false)
      setError(null)
    } else if (
      sendCommand.current &&
      pages.some((page) =>
        page.turns.some(
          (turn) => turn.command_id === sendCommand.current?.command_id
        )
      )
    ) {
      sendCommand.current = null
      setError(null)
    } else if (sendCommand.current || deleteCommand.current) {
      setRetryCommand(true)
    } else setError(null)
  }
  async function send(operation: MeetingAssistantOperation, question: string) {
    if (!canSend || busy.current || !thread || !question.trim()) return
    sendCommand.current = {
      version: 1,
      meeting_ref: meetingId,
      command_id: crypto.randomUUID(),
      operation,
      question: question.trim(),
      expected_thread_version: thread.thread_version,
    }
    await submitSend()
  }
  async function submitSend() {
    const command = sendCommand.current
    if (!command || busy.current) return
    busy.current = true
    setPending(true)
    setRetryCommand(false)
    setError(null)
    const controller = new AbortController()
    request.current = controller
    try {
      await client.cancelQueries({ queryKey })
      const result = await sendAssistantCommand(command, controller.signal)
      if (!active.current) return
      sendCommand.current = null
      replaceThread(result.thread)
    } catch (failure) {
      if (!active.current) return
      if (failure instanceof ApiError && failure.statusCode === 409) {
        sendCommand.current = null
        await query.refetch()
        setError('conflict')
      } else if (
        failure instanceof ApiError &&
        [400, 422].includes(failure.statusCode)
      ) {
        sendCommand.current = null
        setError('rejected')
      } else {
        setError('unconfirmed')
        if (
          failure instanceof ApiError &&
          [401, 403, 404].includes(failure.statusCode)
        )
          await query.refetch()
      }
    } finally {
      busy.current = false
      if (active.current) setPending(false)
    }
  }
  async function remove() {
    if (
      busy.current ||
      query.isFetching ||
      query.isError ||
      !thread ||
      sendCommand.current
    )
      return
    if (!deleteCommand.current)
      deleteCommand.current = {
        version: 1,
        meeting_ref: meetingId,
        command_id: crypto.randomUUID(),
        expected_thread_version: thread.thread_version,
      }
    const command = deleteCommand.current
    busy.current = true
    setPending(true)
    setRetryCommand(false)
    setError(null)
    const controller = new AbortController()
    request.current = controller
    try {
      await client.cancelQueries({ queryKey })
      const receipt = await deleteAssistantThread(command, controller.signal)
      if (!active.current) return
      deleteCommand.current = null
      setConfirmDelete(false)
      replaceThread({
        ...thread,
        thread_version: receipt.thread_version,
        deleted: true,
        turns: [],
        has_more: false,
        next_cursor: null,
      })
    } catch (failure) {
      if (!active.current) return
      if (failure instanceof ApiError && failure.statusCode === 409) {
        deleteCommand.current = null
        setConfirmDelete(false)
        await query.refetch()
        setError('conflict')
      } else {
        setError('unconfirmed')
        if (
          failure instanceof ApiError &&
          [401, 403, 404].includes(failure.statusCode)
        )
          await query.refetch()
      }
    } finally {
      busy.current = false
      if (active.current) setPending(false)
    }
  }
  return {
    query,
    turns,
    pending,
    error,
    canSend,
    canDelete,
    retryCommand,
    confirmDelete,
    refresh: () => void refresh(),
    send: (operation: MeetingAssistantOperation, question: string) =>
      void send(operation, question),
    retry: () => {
      if (sendCommand.current) void submitSend()
      else if (deleteCommand.current) void remove()
    },
    requestDelete: () => setConfirmDelete(true),
    cancelDelete: () => {
      if (!pending) setConfirmDelete(false)
    },
    remove: () => void remove(),
  }
}
