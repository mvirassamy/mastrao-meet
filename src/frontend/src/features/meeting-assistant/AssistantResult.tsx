import { useTranslation } from 'react-i18next'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import { AssistantEmailCard } from './AssistantEmailCard'
import { TypedText } from './AssistantTyping'
import { afterTyping, revealIn } from './typingAnimation'

export type AssistantCitationView = { id: string; label: string }

export type AssistantAnswer =
  | { kind: 'text'; text: string }
  | { kind: 'actions'; actions: { statement: string; details: string }[] }
  | { kind: 'email'; subject: string; body: string }

/** Why a turn shows no answer. */
export type AssistantOutcome =
  | 'abstention'
  | 'refusal'
  | 'unavailable'
  | 'failure'
  | 'processing'
  | 'unknown'

export type AssistantResultView =
  | {
      id: string
      outcome: 'answer'
      answer: AssistantAnswer
      citations: AssistantCitationView[]
      /** A real gap in the sources, read under the answer. */
      sourceLimit: string | null
    }
  | { id: string; outcome: AssistantOutcome }

const mutedLine = css({
  margin: 0,
  fontSize: '0.8125rem',
  lineHeight: '1.5',
  color: 'muted-foreground',
})

const plainText = css({
  margin: 0,
  whiteSpace: 'pre-wrap',
  overflowWrap: 'anywhere',
})

function ActionList({
  actions,
}: {
  actions: Extract<AssistantAnswer, { kind: 'actions' }>['actions']
}) {
  return (
    <ul
      className={css({
        margin: 0,
        padding: 0,
        listStyle: 'none',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.625rem',
      })}
    >
      {actions.map((action) => (
        <li
          key={action.statement}
          className={css({
            paddingLeft: '0.75rem',
            borderLeft: '2px solid token(colors.border)',
            overflowWrap: 'anywhere',
          })}
        >
          {action.statement}
          {action.details && (
            <span
              className={css({
                display: 'block',
                fontSize: '0.75rem',
                color: 'muted-foreground',
              })}
            >
              {action.details}
            </span>
          )}
        </li>
      ))}
    </ul>
  )
}

/** The answer itself; one that just arrived writes its text out. */
function Answer({
  answer,
  arrived,
}: {
  answer: AssistantAnswer
  arrived: boolean
}) {
  const reveal = arrived ? revealIn : undefined
  switch (answer.kind) {
    case 'email':
      return (
        <div className={reveal}>
          <AssistantEmailCard subject={answer.subject} body={answer.body} />
        </div>
      )
    case 'actions':
      return (
        <div className={reveal}>
          <ActionList actions={answer.actions} />
        </div>
      )
    case 'text':
      return (
        <p className={plainText}>
          {arrived ? <TypedText text={answer.text} /> : answer.text}
        </p>
      )
  }
}

export function AssistantResult({
  result,
  arrived = false,
  onOpenCitation,
}: {
  result: AssistantResultView
  /** The answer just received, written out as it arrives. */
  arrived?: boolean
  onOpenCitation: (id: string) => void
}) {
  const { t } = useTranslation('meetingAssistant')
  if (result.outcome !== 'answer')
    return (
      <p role="status" className={mutedLine}>
        {t(`result.${result.outcome}`)}
      </p>
    )
  const answerText = result.answer.kind === 'text' ? result.answer.text : ''
  return (
    <div
      className={css({
        display: 'flex',
        flexDirection: 'column',
        gap: '0.625rem',
        fontSize: '0.875rem',
        lineHeight: '1.6',
        color: 'var(--heading-foreground)',
      })}
    >
      <Answer answer={result.answer} arrived={arrived} />
      {result.citations.length > 0 && (
        <ul
          aria-label={t('result.sources')}
          // The sources follow once a text answer has written itself out.
          style={arrived ? afterTyping(answerText) : undefined}
          className={`${arrived ? revealIn : ''} ${css({
            margin: 0,
            padding: 0,
            listStyle: 'none',
            display: 'flex',
            flexWrap: 'wrap',
            gap: '0.375rem',
          })}`}
        >
          {result.citations.map((citation) => (
            <li key={citation.id}>
              <Button
                size="xs"
                variant="secondary"
                aria-label={t('result.openSource', { label: citation.label })}
                onPress={() => onOpenCitation(citation.id)}
                className={css({
                  borderRadius: 'full!',
                  fontVariantNumeric: 'tabular-nums',
                })}
              >
                {citation.label}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {result.sourceLimit && (
        <p
          className={css({
            margin: 0,
            fontSize: '0.75rem',
            color: 'muted-foreground',
          })}
        >
          {result.sourceLimit}
        </p>
      )}
    </div>
  )
}
