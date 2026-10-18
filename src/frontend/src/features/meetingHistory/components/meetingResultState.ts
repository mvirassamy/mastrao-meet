import type { MeetingContentStatus, MeetingSummary } from '../api/types'
import { contentPhase } from './meetingContent'

/** Local state of the summary request sent when the detail opens. */
export type SummaryRequestState = 'idle' | 'pending' | 'failed'

export const summaryEditorialStatusKey = (
  state: MeetingSummary['productState']
) => {
  switch (state) {
    case 'published_reviewed':
      return 'summary.published'
    case 'generated_pending_review':
      return 'summary.pendingReview'
    case 'validated_ready_to_publish':
      return 'summary.validated'
    case 'stale_draft':
      return 'summary.stale'
    default:
      return 'summary.reviewUnknown'
  }
}

/** Statuses for which the service is still working on the content. */
const isPendingStatus = (status: MeetingContentStatus) =>
  contentPhase(status) === 'pending'

/** What a section shows, which sets the look of its header. */
export type SectionLook = 'content' | 'pending' | 'empty' | 'failed'

export const contentSectionLook = (
  status: MeetingContentStatus
): SectionLook => {
  switch (contentPhase(status)) {
    case 'ready':
    case 'partial':
      return 'content'
    case 'pending':
      return 'pending'
    case 'failed':
      return 'failed'
    case 'absent':
      return 'empty'
  }
}

/** What the summary section shows: a step of the summary request, or the
 * summary status itself. */
export type SummaryView =
  | 'request-pending'
  | 'request-failed'
  | 'waiting-transcript'
  | MeetingContentStatus

export const summaryView = (
  summary: MeetingSummary,
  transcriptStatus: MeetingContentStatus,
  requestState: SummaryRequestState
): SummaryView => {
  if (summary.status !== 'not_started') return summary.status
  if (requestState === 'pending') return 'request-pending'
  if (requestState === 'failed') return 'request-failed'
  if (isPendingStatus(transcriptStatus)) return 'waiting-transcript'
  return 'not_started'
}

export const summarySectionLook = (view: SummaryView): SectionLook => {
  switch (view) {
    case 'request-pending':
    case 'waiting-transcript':
      return 'pending'
    case 'request-failed':
      return 'failed'
    default:
      return contentSectionLook(view)
  }
}

/** Translation key of the status read in the summary section header. */
export const summaryStatusKey = (view: SummaryView) => {
  switch (view) {
    case 'request-pending':
      return 'status.summary.transcribing'
    case 'request-failed':
      return 'summary.requestFailed.title'
    case 'waiting-transcript':
      return 'summary.waitingTranscript.title'
    default:
      return `status.summary.${view}`
  }
}
