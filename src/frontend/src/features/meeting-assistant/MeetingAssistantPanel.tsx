import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, TextArea } from '@/primitives'
import { css } from '@/styled-system/css'
import { AssistantResult, type AssistantResultView } from './AssistantResult'

export type MeetingAssistantAction =
  | 'short-summary'
  | 'actions'
  | 'follow-up-email'
export type AssistantTurnView = {
  id: string
  question: string
  result: AssistantResultView
}

/** Presentation only: authorized history and commands belong to the API hook. */
export function MeetingAssistantPanel({
  turns,
  loading,
  pending,
  error,
  onRetry,
  onAsk,
  onAction,
  onOpenCitation,
}: {
  turns: AssistantTurnView[]
  loading: boolean
  pending: boolean
  error: boolean
  onRetry: () => void
  onAsk: (question: string) => void
  onAction: (action: MeetingAssistantAction) => void
  onOpenCitation: (id: string) => void
}) {
  const { t } = useTranslation('meetingAssistant')
  const [question, setQuestion] = useState('')
  const disabled = loading || pending || error
  return (
    <section
      id="meeting-assistant"
      aria-labelledby="meeting-assistant-title"
      className={css({
        marginTop: '1.25rem',
        border: '1px solid token(colors.border)',
        borderRadius: '12px',
        padding: { base: '1rem', md: '1.5rem' },
        backgroundColor: 'card',
        display: 'flex',
        flexDirection: 'column',
        gap: '1rem',
      })}
    >
      <header>
        <h2
          id="meeting-assistant-title"
          className={css({ fontSize: '1.125rem', fontWeight: 600, margin: 0 })}
        >
          {t('title')}
        </h2>
        <p className={css({ color: 'muted-foreground', fontSize: '0.875rem' })}>
          {t('scope')}
        </p>
      </header>
      {loading && <p role="status">{t('loading')}</p>}
      {error && (
        <div role="alert">
          <p>{t('error')}</p>
          <Button variant="secondary" onPress={onRetry}>
            {t('retry')}
          </Button>
        </div>
      )}
      {!loading && !error && (
        <>
          {turns.length === 0 && <p>{t('empty')}</p>}
          <Button
            variant="ghost"
            size="sm"
            isDisabled={pending}
            onPress={onRetry}
          >
            {t('retry')}
          </Button>
          <ol
            aria-label={t('history')}
            className={css({
              padding: 0,
              listStyle: 'none',
              display: 'flex',
              flexDirection: 'column',
              gap: '1.5rem',
            })}
          >
            {turns.map((turn) => (
              <li key={turn.id}>
                <h3
                  className={css({
                    fontSize: '0.9375rem',
                    fontWeight: 600,
                    overflowWrap: 'anywhere',
                  })}
                >
                  {turn.question}
                </h3>
                <AssistantResult
                  key={turn.result.id}
                  result={turn.result}
                  onOpenCitation={onOpenCitation}
                />
              </li>
            ))}
          </ol>
        </>
      )}
      {pending && <p role="status">{t('pending')}</p>}
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (!disabled && question.trim()) onAsk(question.trim())
        }}
        className={css({
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-start',
          gap: '0.5rem',
        })}
      >
        <label htmlFor="meeting-question">{t('question')}</label>
        <TextArea
          id="meeting-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          rows={3}
          maxLength={1024}
          disabled={disabled}
          className={css({
            padding: '0.75rem',
            backgroundColor: 'card',
            resize: 'vertical',
          })}
        />
        <Button type="submit" isDisabled={disabled || !question.trim()}>
          {t('ask')}
        </Button>
      </form>
      <div
        aria-label={t('actions.title')}
        role="group"
        className={css({ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' })}
      >
        {(['short-summary', 'actions', 'follow-up-email'] as const).map(
          (action) => (
            <Button
              key={action}
              variant="outline"
              isDisabled={disabled}
              onPress={() => onAction(action)}
            >
              {t(`actions.${action}`)}
            </Button>
          )
        )}
      </div>
    </section>
  )
}
