import { describe, expect, it } from 'vitest'
import {
  assistantThread,
  assistantReceipt,
  openedAssistantCitation,
} from '@/features/meeting-assistant/assistantResponse'
import type {
  MeetingAssistantCitation,
  MeetingAssistantSendRequest,
  MeetingAssistantThread,
  MeetingAssistantTurn,
} from '@/features/meeting-assistant/types'

const meetingId = 'meeting_0123456789abcdef'
const thread: MeetingAssistantThread = {
  version: 1,
  meeting_ref: meetingId,
  thread_ref: null,
  thread_version: 0,
  visibility: 'private',
  deleted: false,
  turns: [],
  has_more: false,
  next_cursor: null,
}
const citation: MeetingAssistantCitation = {
  citation_id: 'citation_0123456789abcdef',
  meeting_ref: meetingId,
  source_kind: 'reviewed_revision',
  source_ref: 'source_0123456789abcdef',
  source_version: 1,
  source_digest: 'a'.repeat(64),
  segment_id: 'segment-1',
  start_ms: 0,
  end_ms: 1000,
  time_basis: 'meeting_relative_ms',
}
const command: MeetingAssistantSendRequest = {
  version: 1,
  meeting_ref: meetingId,
  command_id: 'command_0123456789abcdef',
  expected_thread_version: 0,
  operation: 'question',
  question: 'Quel budget ?',
}
const turn: MeetingAssistantTurn = {
  command_id: command.command_id,
  operation: 'question',
  question: command.question,
  state: 'processing',
  result: null,
  coverage: null,
  created_at: 1,
}

describe('private assistant response binding', () => {
  it('rejects a thread from another meeting or a shared conversation', () => {
    expect(assistantThread(thread, meetingId)).toBe(thread)
    expect(() =>
      assistantThread(
        { ...thread, meeting_ref: 'meeting_other0123456789' },
        meetingId
      )
    ).toThrow()
    expect(() =>
      assistantThread(
        { ...thread, visibility: 'shared' as 'private' },
        meetingId
      )
    ).toThrow()
  })
  it('rejects an acknowledgement for another command or operation', () => {
    const receipt = {
      version: 1 as const,
      thread,
      turn,
      idempotent_replay: false,
    }
    expect(assistantReceipt(receipt, command)).toBe(receipt)
    expect(() =>
      assistantReceipt(
        {
          ...receipt,
          turn: { ...turn, command_id: 'command_other0123456789' },
        },
        command
      )
    ).toThrow()
    expect(() =>
      assistantReceipt(
        { ...receipt, turn: { ...turn, operation: 'email_draft' } },
        command
      )
    ).toThrow()
  })
  it('opens the exact source descriptor and rejects a replacement revision or passage', () => {
    const response = {
      version: 1 as const,
      segment: {
        citation,
        text: 'Passage relu.',
        speaker: null,
        status: 'transcript' as const,
      },
    }
    expect(openedAssistantCitation(response, citation)).toBe(response.segment)
    for (const replacement of [
      { source_version: 2 },
      { source_digest: 'b'.repeat(64) },
      { segment_id: 'segment-2' },
      { start_ms: 500 },
      { meeting_ref: 'meeting_other0123456789' },
    ]) {
      expect(() =>
        openedAssistantCitation(
          {
            ...response,
            segment: {
              ...response.segment,
              citation: { ...citation, ...replacement },
            },
          },
          citation
        )
      ).toThrow()
    }
  })
})
