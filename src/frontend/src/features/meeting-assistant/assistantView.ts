import type { TFunction } from 'i18next'
import { formatTranscriptTimestamp } from '@/features/meetingHistory/utils/meetingHistoryFormat'
import type { AssistantTurnView } from './MeetingAssistantPanel'
import type {
  AssistantAnswer,
  AssistantOutcome,
  AssistantResultView,
} from './AssistantResult'
import type {
  MeetingAssistantCitation,
  MeetingAssistantCoverage,
  MeetingAssistantResult,
  MeetingAssistantTurn,
} from './types'

const answeredResult = (turn: MeetingAssistantTurn) =>
  turn.state === 'completed' && turn.result?.state === 'answered'
    ? turn.result
    : null

/** Read only for a turn without answer. */
function outcome(turn: MeetingAssistantTurn): AssistantOutcome {
  switch (turn.state) {
    case 'processing':
      return 'processing'
    case 'failed':
      return 'failure'
    case 'unknown':
      return 'unknown'
    case 'sources_unavailable':
      return 'unavailable'
    case 'refused':
      return 'refusal'
    case 'completed':
      if (turn.result?.state === 'abstained') return 'abstention'
      if (turn.result?.state === 'refused') return 'refusal'
      return 'unavailable'
  }
}

/** The answer itself: an e-mail, a list of actions or plain text. */
function answer(
  turn: MeetingAssistantTurn,
  result: MeetingAssistantResult,
  t: TFunction
): AssistantAnswer {
  if (turn.operation === 'email_draft' && result.email_draft)
    return { kind: 'email', ...result.email_draft }
  if (turn.operation === 'actions' && result.actions.length > 0)
    return {
      kind: 'actions',
      actions: result.actions.map((action) => ({
        statement: action.statement,
        details: [
          action.owner,
          action.due_date && t('result.due', { date: action.due_date }),
        ]
          .filter(Boolean)
          .join(' · '),
      })),
    }
  return { kind: 'text', text: result.text }
}

/** Only a real gap in the sources is worth a line under the answer. */
function sourceLimit(
  coverage: MeetingAssistantCoverage | null,
  t: TFunction
): string | null {
  if (!coverage || coverage.source_coverage === 'reference_transcript')
    return null
  return t(`limit.${coverage.source_coverage}`)
}

const citationLabel = (citation: MeetingAssistantCitation, t: TFunction) =>
  citation.source_kind === 'published_brief'
    ? t('result.brief')
    : formatTranscriptTimestamp(citation.start_ms)

function resultView(
  turn: MeetingAssistantTurn,
  t: TFunction
): AssistantResultView {
  const result = answeredResult(turn)
  if (!result) return { id: turn.command_id, outcome: outcome(turn) }
  return {
    id: turn.command_id,
    outcome: 'answer',
    answer: answer(turn, result, t),
    citations: result.citations.map((citation) => ({
      id: citation.citation_id,
      label: citationLabel(citation, t),
    })),
    sourceLimit: sourceLimit(turn.coverage, t),
  }
}

export function assistantTurnView(
  turn: MeetingAssistantTurn,
  t: TFunction
): AssistantTurnView {
  const question =
    turn.operation === 'question' && turn.question
      ? turn.question
      : t(`operation.${turn.operation}`)
  return {
    id: turn.command_id,
    question,
    result: resultView(turn, t),
  }
}
