import { useTranslation } from 'react-i18next'
import { CheckIcon } from '@/icons'
import { MeetingSectionState } from '@/features/meetingHistory/components/MeetingHistoryStatePanel'
import { ASSISTANT_ICON } from '@/features/meetingHistory/components/meetingContent'
import { css, cva } from '@/styled-system/css'
import type { AssistantReadiness } from './assistantReadiness'

type StepState = 'done' | 'current' | 'waiting'

const PREPARATION_STEPS = [
  { key: 'ended', state: 'done' },
  { key: 'transcribing', state: 'current' },
  { key: 'ready', state: 'waiting' },
] as const satisfies { key: string; state: StepState }[]

const stepMark = cva({
  base: {
    display: 'grid',
    placeItems: 'center',
    width: '16px',
    height: '16px',
    borderRadius: 'full',
    flexShrink: 0,
  },
  variants: {
    state: {
      done: { backgroundColor: '#2f9e5a', color: 'white' },
      current: {
        border: '2px solid token(colors.primary)',
        '& span': {
          width: '6px',
          height: '6px',
          borderRadius: 'full',
          backgroundColor: 'primary',
          animation: 'fade 0.9s ease-in-out infinite alternate',
          _motionReduce: { animation: 'none' },
        },
      },
      waiting: { border: '2px solid token(colors.border)' },
    },
  },
})

/** The assistant icon turning in the centre, with what it waits for. */
function AssistantPreparing() {
  const { t } = useTranslation('meetingAssistant')
  return (
    <MeetingSectionState
      icon={ASSISTANT_ICON}
      look="pending"
      title={t('preparing.title')}
      description={t('preparing.description')}
      action={
        <ol
          className={css({
            margin: 0,
            padding: 0,
            listStyle: 'none',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.5rem',
            textAlign: 'left',
            fontSize: '0.8125rem',
          })}
        >
          {PREPARATION_STEPS.map((step) => (
            <li
              key={step.key}
              aria-current={step.state === 'current' ? 'step' : undefined}
              className={css({
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                color:
                  step.state === 'waiting'
                    ? 'muted-foreground'
                    : 'var(--heading-foreground)',
              })}
            >
              <span
                aria-hidden="true"
                className={stepMark({ state: step.state })}
              >
                {step.state === 'done' && <CheckIcon size={12} />}
                {step.state === 'current' && <span />}
              </span>
              {t(`preparing.${step.key}`)}
              {/* The other labels already say their state; this one would
                  read as done without its mark. */}
              {step.state === 'waiting' && (
                <span className={css({ srOnly: true })}>
                  {t('preparing.upcoming')}
                </span>
              )}
            </li>
          ))}
        </ol>
      }
    />
  )
}

/** What the panel shows before the assistant can answer. */
export function AssistantNotReady({
  readiness,
}: {
  readiness: Exclude<AssistantReadiness, 'ready'>
}) {
  const { t } = useTranslation('meetingAssistant')
  if (readiness === 'preparing') return <AssistantPreparing />
  return (
    <MeetingSectionState
      icon={ASSISTANT_ICON}
      look="empty"
      title={t('unavailable.title')}
      description={t('unavailable.description')}
    />
  )
}
