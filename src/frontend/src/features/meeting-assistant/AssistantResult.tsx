import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, TextArea } from '@/primitives'
import { css } from '@/styled-system/css'

export type AssistantCitationView = { id: string; label: string }
export type AssistantResultView = {
  id: string
  outcome:
    | 'answer'
    | 'abstention'
    | 'refusal'
    | 'unavailable'
    | 'failure'
    | 'processing'
    | 'unknown'
  text: string
  coverage: string[]
  citations: AssistantCitationView[]
  editable: boolean
}

/** Edits stay in this private view and never mutate the official summary. */
export function AssistantResult({
  result,
  onOpenCitation,
}: {
  result: AssistantResultView
  onOpenCitation: (id: string) => void
}) {
  const { t } = useTranslation('meetingAssistant')
  if (
    ['unavailable', 'failure', 'processing', 'unknown'].includes(result.outcome)
  )
    return <p role="status">{t(`result.${result.outcome}`)}</p>
  return (
    <div
      className={css({
        display: 'flex',
        flexDirection: 'column',
        gap: '0.75rem',
      })}
    >
      {result.outcome === 'abstention' && (
        <p role="status">{t('result.abstention')}</p>
      )}
      {result.outcome === 'refusal' && (
        <p role="status">{t('result.refusal')}</p>
      )}
      {result.coverage.length > 0 && (
        <ul aria-label={t('result.coverage')}>
          {result.coverage.map((limit) => (
            <li key={limit}>{limit}</li>
          ))}
        </ul>
      )}
      {result.editable ? (
        <EditableProposal key={result.text} initialText={result.text} />
      ) : (
        <p
          className={css({
            whiteSpace: 'pre-wrap',
            overflowWrap: 'anywhere',
            margin: 0,
            lineHeight: 1.6,
          })}
        >
          {result.text}
        </p>
      )}
      {result.citations.length > 0 && (
        <ul
          aria-label={t('result.sources')}
          className={css({
            padding: 0,
            listStyle: 'none',
            display: 'flex',
            flexWrap: 'wrap',
            gap: '0.5rem',
          })}
        >
          {result.citations.map((citation) => (
            <li key={citation.id}>
              <Button
                size="sm"
                variant="outline"
                onPress={() => onOpenCitation(citation.id)}
              >
                {citation.label}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function EditableProposal({ initialText }: { initialText: string }) {
  const { t } = useTranslation('meetingAssistant')
  const [text, setText] = useState(initialText)
  const [copy, setCopy] = useState<'idle' | 'copied' | 'failed'>('idle')
  async function copyText() {
    try {
      await navigator.clipboard.writeText(text)
      setCopy('copied')
    } catch {
      setCopy('failed')
    }
  }
  return (
    <>
      <label>
        {t('result.edit')}
        <TextArea
          aria-label={t('result.edit')}
          value={text}
          onChange={(event) => {
            setText(event.target.value)
            setCopy('idle')
          }}
          rows={8}
          className={css({
            resize: 'vertical',
            minHeight: '10rem',
            padding: '0.75rem',
            backgroundColor: 'card',
          })}
        />
      </label>
      <p className={css({ color: 'muted-foreground', fontSize: '0.8125rem' })}>
        {t('result.proposal')}
      </p>
      <Button size="sm" variant="secondary" onPress={() => void copyText()}>
        {t('result.copy')}
      </Button>
      {copy !== 'idle' && <p role="status">{t(`result.${copy}`)}</p>}
    </>
  )
}
