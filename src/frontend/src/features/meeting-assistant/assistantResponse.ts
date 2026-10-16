import type {
  MeetingAssistantCitation,
  MeetingAssistantDeleteResponse,
  MeetingAssistantOpenCitationResponse,
  MeetingAssistantSendRequest,
  MeetingAssistantSendResponse,
  MeetingAssistantThread,
} from './types'

/** The bridge validates the wire schema; these checks bind it to this view. */
export function assistantThread(
  thread: MeetingAssistantThread,
  meetingId: string
): MeetingAssistantThread {
  if (
    thread.version !== 1 ||
    thread.meeting_ref !== meetingId ||
    thread.visibility !== 'private' ||
    !Number.isSafeInteger(thread.thread_version) ||
    thread.thread_version < 0 ||
    !Array.isArray(thread.turns) ||
    thread.turns.length > 20 ||
    thread.turns.some((turn) =>
      turn.result?.citations.some(
        (citation) => citation.meeting_ref !== meetingId
      )
    )
  )
    throw new Error('Invalid private meeting thread')
  return thread
}

export function assistantReceipt(
  receipt: MeetingAssistantSendResponse,
  command: MeetingAssistantSendRequest
) {
  const thread = assistantThread(receipt.thread, command.meeting_ref)
  if (
    receipt.version !== 1 ||
    typeof receipt.idempotent_replay !== 'boolean' ||
    receipt.turn.command_id !== command.command_id ||
    receipt.turn.operation !== command.operation ||
    thread.thread_version < command.expected_thread_version ||
    receipt.turn.result?.citations.some(
      (citation) => citation.meeting_ref !== command.meeting_ref
    )
  )
    throw new Error('Invalid meeting assistant receipt')
  return receipt
}

export function openedAssistantCitation(
  response: MeetingAssistantOpenCitationResponse,
  citation: MeetingAssistantCitation
) {
  const opened = response.segment.citation
  const fields = Object.keys(citation) as (keyof MeetingAssistantCitation)[]
  if (
    response.version !== 1 ||
    Object.keys(opened).length !== fields.length ||
    fields.some((field) => citation[field] !== opened[field])
  )
    throw new Error('The cited source or its version has changed')
  return response.segment
}

export function deletedAssistantThread(
  response: MeetingAssistantDeleteResponse,
  expectedVersion: number
) {
  if (
    response.version !== 1 ||
    response.deleted !== true ||
    !Number.isSafeInteger(response.thread_version) ||
    response.thread_version < expectedVersion
  )
    throw new Error('Invalid private thread deletion receipt')
  return response
}
