import type { TFunction } from 'i18next'
import { formatTranscriptTimestamp } from '@/features/meetingHistory/utils/meetingHistoryFormat'
import type { AssistantTurnView } from './MeetingAssistantPanel'
import type { AssistantResultView } from './AssistantResult'
import type { MeetingAssistantTurn } from './types'

function outcome(turn: MeetingAssistantTurn): AssistantResultView['outcome'] {
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
      if (!turn.result) return 'unavailable'
      if (turn.result.state === 'abstained') return 'abstention'
      if (turn.result.state === 'refused') return 'refusal'
      return 'answer'
  }
}

export function assistantTurnView(
  turn: MeetingAssistantTurn,
  t: TFunction
): AssistantTurnView {
  const state = outcome(turn)
  const result = turn.result
  let text = result?.text ?? ''
  if (
    state === 'answer' &&
    turn.operation === 'email_draft' &&
    result?.email_draft
  ) {
    text = `${t('result.subject')}: ${result.email_draft.subject}\n\n${result.email_draft.body}`
  }
  if (
    state === 'answer' &&
    turn.operation === 'actions' &&
    result?.actions.length
  ) {
    text = result.actions
      .map(
        (action) =>
          `${action.statement}\n${t('result.owner')}: ${action.owner ?? t('result.unspecified')}\n${t('result.due')}: ${action.due_date ?? t('result.unspecified')}`
      )
      .join('\n\n')
  }
  const coverage: string[] = []
  if (turn.coverage) {
    coverage.push(
      t(`coverage.state.${turn.coverage.state}`),
      t(`coverage.source.${turn.coverage.source_coverage}`),
      t(`coverage.selection.${turn.coverage.selection}`)
    )
    if (turn.coverage.omitted_segments > 0)
      coverage.push(
        t('coverage.omitted', { count: turn.coverage.omitted_segments })
      )
  } else {
    coverage.push(t('coverage.unknown'))
  }
  const readable =
    state === 'answer' || state === 'abstention' || state === 'refusal'
  let questionLabel = t(`operation.${turn.operation}`)
  if (turn.operation === 'question' && turn.question)
    questionLabel = turn.question
  return {
    id: turn.command_id,
    question: questionLabel,
    result: {
      id: turn.command_id,
      outcome: state,
      text: readable ? text : '',
      coverage: readable ? coverage : [],
      citations: readable
        ? (result?.citations ?? []).map((citation) => ({
            id: citation.citation_id,
            label: `${t(`source.kind.${citation.source_kind}`)} · ${formatTranscriptTimestamp(citation.start_ms)} · ${t('source.version', { version: citation.source_version })}`,
          }))
        : [],
      editable: state === 'answer' && turn.operation !== 'question',
    },
  }
}
