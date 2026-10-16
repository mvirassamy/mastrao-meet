import { fetchApi } from '@/api/fetchApi'
import {
  assistantThread,
  assistantReceipt,
  deletedAssistantThread,
  openedAssistantCitation,
} from './assistantResponse'
import type {
  MeetingAssistantCitation,
  MeetingAssistantDeleteRequest,
  MeetingAssistantDeleteResponse,
  MeetingAssistantOpenCitationResponse,
  MeetingAssistantSendRequest,
  MeetingAssistantSendResponse,
  MeetingAssistantThread,
} from './types'

const assistantPath = (meetingId: string) =>
  `meetings/history/${encodeURIComponent(meetingId)}/assistant/`

export async function readAssistantThread(
  meetingId: string,
  before: string | null,
  signal: AbortSignal
) {
  const query = new URLSearchParams({ limit: '20' })
  if (before) query.set('before_command_id', before)
  return assistantThread(
    await fetchApi<MeetingAssistantThread>(
      `${assistantPath(meetingId)}?${query}`,
      {
        signal,
        cache: 'no-store',
      }
    ),
    meetingId
  )
}

export async function sendAssistantCommand(
  command: MeetingAssistantSendRequest,
  signal: AbortSignal
) {
  return assistantReceipt(
    await fetchApi<MeetingAssistantSendResponse>(
      assistantPath(command.meeting_ref),
      {
        method: 'POST',
        body: JSON.stringify(command),
        signal,
      }
    ),
    command
  )
}

export async function deleteAssistantThread(
  command: MeetingAssistantDeleteRequest,
  signal: AbortSignal
) {
  return deletedAssistantThread(
    await fetchApi<MeetingAssistantDeleteResponse>(
      assistantPath(command.meeting_ref),
      {
        method: 'DELETE',
        body: JSON.stringify(command),
        signal,
      }
    ),
    command.expected_thread_version
  )
}

export async function openAssistantCitation(
  citation: MeetingAssistantCitation,
  signal: AbortSignal
) {
  return openedAssistantCitation(
    await fetchApi<MeetingAssistantOpenCitationResponse>(
      `${assistantPath(citation.meeting_ref)}citation/`,
      {
        method: 'POST',
        body: JSON.stringify({
          version: 1,
          meeting_ref: citation.meeting_ref,
          command_id: crypto.randomUUID(),
          citation,
        }),
        signal,
        cache: 'no-store',
      }
    ),
    citation
  )
}
