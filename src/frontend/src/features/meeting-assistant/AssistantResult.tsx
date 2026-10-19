import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { InformationIcon } from '@/icons'
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

const mutedLine = css({
  margin: 0,
  fontSize: '0.8125rem',
  lineHeight: '1.5',
  color: 'muted-foreground',
})

/** Actions read as links aligned with the answer text. */
const inlineAction = css({ paddingX: '0!', minHeight: 'auto!' })

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
    return (
      <p role="status" className={mutedLine}>
        {t(`result.${result.outcome}`)}
      </p>
    )
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
      {result.outcome === 'abstention' && (
        <p role="status">{t('result.abstention')}</p>
      )}
      {result.outcome === 'refusal' && (
        <p role="status">{t('result.refusal')}</p>
      )}
      {result.editable ? (
        <EditableProposal key={result.text} initialText={result.text} />
      ) : (
        <p
          className={css({
            whiteSpace: 'pre-wrap',
            overflowWrap: 'anywhere',
            margin: 0,
          })}
        >
          {result.text}
        </p>
      )}
      {result.citations.length > 0 && (
        <ul
          aria-label={t('result.sources')}
          className={css({
            margin: 0,
            padding: 0,
            listStyle: 'none',
            display: 'flex',
            flexWrap: 'wrap',
            gap: '0.375rem',
          })}
        >
          {result.citations.map((citation) => (
            <li key={citation.id}>
              <Button
                size="xs"
                variant="secondary"
                onPress={() => onOpenCitation(citation.id)}
              >
                {citation.label}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {result.coverage.length > 0 && (
        <p
          className={`${mutedLine} ${css({
            display: 'flex',
            alignItems: 'flex-start',
            gap: '0.375rem',
            fontSize: '0.75rem',
          })}`}
        >
          <InformationIcon
            size={12}
            aria-hidden="true"
            className={css({ flexShrink: 0, marginTop: '0.2rem' })}
          />
          <span>
            <span className={css({ srOnly: true })}>
              {t('result.coverage')} :{' '}
            </span>
            {result.coverage.join(' ')}
          </span>
        </p>
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
    <div
      className={css({
        border: '1px solid token(colors.border)',
        borderRadius: '12px',
        overflow: 'hidden',
      })}
    >
      <TextArea
        aria-label={t('result.edit')}
        value={text}
        onChange={(event) => {
          setText(event.target.value)
          setCopy('idle')
        }}
        fieldSizing="content"
        css={{
          border: 'none',
          borderRadius: 0,
          resize: 'none',
          minHeight: '6rem',
          maxHeight: '24rem',
          padding: '0.75rem',
          fontSize: '0.8125rem',
          lineHeight: '1.6',
          color: 'var(--heading-foreground)',
          backgroundColor: 'card',
        }}
      />
      <div
        className={css({
          display: 'flex',
          flexDirection: 'column',
          gap: '0.25rem',
          padding: '0.5rem 0.75rem',
          borderTop: '1px solid token(colors.border)',
          backgroundColor: 'muted',
        })}
      >
        <p className={mutedLine}>{t('result.proposal')}</p>
        <div
          className={css({
            display: 'flex',
            alignItems: 'center',
            gap: '0.75rem',
          })}
        >
          <Button
            variant="link"
            size="xs"
            className={inlineAction}
            onPress={() => void copyText()}
          >
            {t('result.copy')}
          </Button>
          {copy !== 'idle' && (
            <p role="status" className={mutedLine}>
              {t(`result.${copy}`)}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
