import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowUpIcon,
  CloseIcon,
  DeleteIcon,
  ListIcon,
  LockIcon,
  MailIcon,
  MoreHorizontalIcon,
  SparklesIcon,
} from '@/icons'
import { Button, Menu, MenuList, TextArea } from '@/primitives'
import { ASSISTANT_ICON } from '@/features/meetingHistory/components/meetingContent'
import { css } from '@/styled-system/css'
import { AssistantResult, type AssistantResultView } from './AssistantResult'
import { AssistantNotReady } from './AssistantNotReady'
import type { AssistantReadiness } from './assistantReadiness'

export type MeetingAssistantAction =
  | 'short-summary'
  | 'actions'
  | 'follow-up-email'
export type AssistantTurnView = {
  id: string
  question: string
  result: AssistantResultView
}

const ACTION_ICONS = {
  'short-summary': SparklesIcon,
  actions: ListIcon,
  'follow-up-email': MailIcon,
} as const

function PrivacyLine() {
  const { t } = useTranslation('meetingAssistant')
  return (
    <span
      className={css({
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.375rem',
        fontSize: '0.75rem',
        color: 'muted-foreground',
      })}
    >
      <LockIcon size={12} aria-hidden="true" />
      {t('privacy')}
    </span>
  )
}

/** The only conversation action: clearing it, after a confirmation. */
function ConversationMenu({
  canClear,
  onClear,
}: {
  canClear: boolean
  onClear: () => void
}) {
  const { t } = useTranslation('meetingAssistant')
  return (
    <Menu density="app">
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={t('options')}
        tooltip={t('options')}
      >
        <MoreHorizontalIcon aria-hidden="true" />
      </Button>
      <MenuList
        aria-label={t('options')}
        disabledKeys={canClear ? [] : ['clear']}
        items={[
          {
            value: 'clear',
            label: (
              <span
                className={css({
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  whiteSpace: 'nowrap',
                })}
              >
                <DeleteIcon size={16} aria-hidden="true" />
                {t('delete.open')}
              </span>
            ),
          },
        ]}
        onAction={onClear}
      />
    </Menu>
  )
}

function Composer({
  readiness,
  disabled,
  onAsk,
  onAction,
}: {
  readiness: AssistantReadiness
  disabled: boolean
  onAsk: (question: string) => void
  onAction: (action: MeetingAssistantAction) => void
}) {
  const { t } = useTranslation('meetingAssistant')
  const [question, setQuestion] = useState('')
  const submit = () => {
    if (!disabled && question.trim()) onAsk(question.trim())
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
      className={css({
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        padding: '0.625rem 0.75rem',
        border: '1px solid token(colors.border)',
        borderRadius: '16px',
        backgroundColor: 'card',
        boxShadow: '0 10px 30px -18px rgba(8, 20, 46, 0.35)',
        // Greyed while the assistant waits for the meeting.
        '&:has(textarea:disabled)': {
          backgroundColor: 'muted',
          boxShadow: 'none',
        },
      })}
    >
      <label htmlFor="meeting-question" className={css({ srOnly: true })}>
        {t('question')}
      </label>
      <TextArea
        id="meeting-question"
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        onKeyDown={(event) => {
          // Enter sends, Shift+Enter keeps a new line.
          if (event.key !== 'Enter' || event.shiftKey) return
          event.preventDefault()
          submit()
        }}
        placeholder={t(`placeholder.${readiness}`)}
        rows={1}
        maxLength={1024}
        disabled={readiness !== 'ready'}
        // Read-only rather than disabled while an answer is pending, so
        // the field keeps the focus after Enter sends the question.
        readOnly={disabled}
        aria-disabled={disabled || undefined}
        fieldSizing="content"
        css={{
          border: 'none',
          padding: '0.25rem 0.125rem',
          resize: 'none',
          maxHeight: '8rem',
          fontSize: '0.875rem',
          lineHeight: '1.5',
          color: 'var(--heading-foreground)',
          backgroundColor: 'transparent',
          _placeholder: { color: 'muted-foreground' },
          _focusVisible: { outline: 'none!' },
        }}
      />
      <div
        role="group"
        aria-label={t('actions.title')}
        className={css({ display: 'flex', flexWrap: 'wrap', gap: '0.375rem' })}
      >
        {(['short-summary', 'actions', 'follow-up-email'] as const).map(
          (action) => {
            const Icon = ACTION_ICONS[action]
            return (
              <Button
                key={action}
                variant="outline"
                size="xs"
                isDisabled={disabled}
                onPress={() => onAction(action)}
                className={css({ borderRadius: 'full!' })}
              >
                <Icon aria-hidden="true" />
                {t(`actions.${action}`)}
              </Button>
            )
          }
        )}
      </div>
      <div
        className={css({
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '0.5rem',
        })}
      >
        <span
          className={css({ fontSize: '0.75rem', color: 'muted-foreground' })}
        >
          {t('scope')}
        </span>
        <Button
          type="submit"
          size="icon-sm"
          aria-label={t('ask')}
          isDisabled={disabled || !question.trim()}
          className={css({ borderRadius: 'full!' })}
        >
          <ArrowUpIcon aria-hidden="true" />
        </Button>
      </div>
    </form>
  )
}

const userBubble = css({
  alignSelf: 'flex-end',
  maxWidth: '85%',
  margin: 0,
  padding: '0.5rem 0.875rem',
  borderRadius: '16px 16px 4px 16px',
  backgroundColor: 'primary',
  color: 'primary.text',
  fontSize: '0.875rem',
  lineHeight: '1.5',
  overflowWrap: 'anywhere',
})

/** Presentation only: authorized history and commands belong to the API hook. */
export function MeetingAssistantPanel({
  readiness,
  turns,
  loading,
  pending,
  error,
  onRetry,
  onAsk,
  onAction,
  onOpenCitation,
  onClear,
  canClear,
  onClose,
  canSend,
  children,
}: {
  readiness: AssistantReadiness
  turns: AssistantTurnView[]
  loading: boolean
  pending: boolean
  error: boolean
  onRetry: () => void
  onAsk: (question: string) => void
  onAction: (action: MeetingAssistantAction) => void
  onOpenCitation: (turnId: string, id: string) => void
  onClear: () => void
  canClear: boolean
  onClose: () => void
  canSend: boolean
  children?: ReactNode
}) {
  const { t } = useTranslation('meetingAssistant')
  const ready = readiness === 'ready'
  const disabled = !ready || loading || pending || error || !canSend
  return (
    <section
      id="meeting-assistant"
      aria-labelledby="meeting-assistant-title"
      className={css({
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        backgroundColor: 'card',
        overflow: 'hidden',
      })}
    >
      <header
        className={css({
          display: 'flex',
          alignItems: 'center',
          gap: '0.625rem',
          minHeight: '64px',
          padding: '0.75rem 0.75rem 0.75rem 1rem',
          borderBottom: '1px solid token(colors.border)',
        })}
      >
        <img
          src={ASSISTANT_ICON}
          alt=""
          aria-hidden="true"
          width={32}
          height={32}
        />
        <div className={css({ flex: 1, minWidth: 0 })}>
          <h2
            id="meeting-assistant-title"
            className={css({
              margin: 0,
              fontSize: '0.9375rem',
              fontWeight: 600,
              color: 'var(--heading-foreground)',
            })}
          >
            {t('title')}
          </h2>
          <PrivacyLine />
        </div>
        {/* A conversation stays clearable once the meeting is processed,
            even when it has no transcript to answer from. */}
        {readiness !== 'preparing' && turns.length > 0 && (
          <ConversationMenu canClear={canClear} onClear={onClear} />
        )}
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t('close')}
          tooltip={t('close')}
          onPress={onClose}
        >
          <CloseIcon aria-hidden="true" />
        </Button>
      </header>
      <div
        className={css({
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          padding: '1rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '1rem',
        })}
      >
        {readiness === 'ready' ? (
          <>
            {loading && (
              <p role="status" className={css({ color: 'muted-foreground' })}>
                {t('loading')}
              </p>
            )}
            {error && (
              <div
                role="alert"
                className={css({
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'flex-start',
                  gap: '0.5rem',
                })}
              >
                <p>{t('error')}</p>
                <Button variant="secondary" size="sm" onPress={onRetry}>
                  {t('retry')}
                </Button>
              </div>
            )}
            {!error && !loading && turns.length === 0 && (
              <div
                className={css({
                  margin: 'auto 0',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: '0.5rem',
                  textAlign: 'center',
                  padding: '1rem 0.5rem',
                })}
              >
                <img
                  src={ASSISTANT_ICON}
                  alt=""
                  aria-hidden="true"
                  width={56}
                  height={56}
                />
                <p
                  className={css({
                    fontWeight: 500,
                    color: 'var(--heading-foreground)',
                  })}
                >
                  {t('empty')}
                </p>
                <p
                  className={css({
                    fontSize: '0.8125rem',
                    color: 'muted-foreground',
                  })}
                >
                  {t('emptyHint')}
                </p>
              </div>
            )}
            {!error && (
              <ol
                hidden={loading}
                aria-label={t('history')}
                className={css({
                  margin: 0,
                  padding: 0,
                  listStyle: 'none',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '1.25rem',
                })}
              >
                {turns.map((turn) => (
                  <li
                    key={turn.id}
                    className={css({
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '0.75rem',
                    })}
                  >
                    <p className={userBubble}>{turn.question}</p>
                    <div
                      className={css({
                        display: 'flex',
                        alignItems: 'flex-start',
                        gap: '0.625rem',
                      })}
                    >
                      <img
                        src={ASSISTANT_ICON}
                        alt=""
                        aria-hidden="true"
                        width={24}
                        height={24}
                        className={css({ flexShrink: 0 })}
                      />
                      <div className={css({ flex: 1, minWidth: 0 })}>
                        <AssistantResult
                          key={turn.result.id}
                          result={turn.result}
                          onOpenCitation={(id) => onOpenCitation(turn.id, id)}
                        />
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            )}
            {pending && (
              <p
                role="status"
                className={css({
                  fontSize: '0.8125rem',
                  color: 'muted-foreground',
                })}
              >
                {t('pending')}
              </p>
            )}
            {children}
          </>
        ) : (
          <div className={css({ margin: 'auto 0' })}>
            <AssistantNotReady readiness={readiness} />
          </div>
        )}
      </div>
      <div className={css({ padding: '0 0.75rem 0.75rem' })}>
        <Composer
          readiness={readiness}
          disabled={disabled}
          onAsk={onAsk}
          onAction={onAction}
        />
      </div>
    </section>
  )
}
