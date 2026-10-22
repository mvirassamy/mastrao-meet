import type { MeetingContentStatus } from '@/features/meetingHistory/api/types'
import { contentPhase } from '@/features/meetingHistory/components/meetingContent'

/** The assistant answers from the transcript, so it waits for it. */
export type AssistantReadiness = 'preparing' | 'ready' | 'unavailable'

export const assistantReadiness = (
  transcriptStatus: MeetingContentStatus
): AssistantReadiness => {
  switch (contentPhase(transcriptStatus)) {
    case 'ready':
    case 'partial':
      return 'ready'
    case 'pending':
      return 'preparing'
    case 'failed':
    case 'absent':
      return 'unavailable'
  }
}
