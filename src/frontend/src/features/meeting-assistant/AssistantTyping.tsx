import { useTranslation } from 'react-i18next'
import { ASSISTANT_ICON } from '@/features/meetingHistory/components/meetingContent'
import { css } from '@/styled-system/css'
import { animationDelay, wordStep } from './typingAnimation'

const wordIn = css({
  animation: 'word_in 0.25s ease-out both',
  _motionReduce: { animation: 'none' },
})

/**
 * An answer that writes itself out word by word, once, when it arrives. The
 * full text is in the page from the start; only its words fade in.
 */
export function TypedText({ text }: { text: string }) {
  const step = wordStep(text)
  let word = 0
  return (
    <>
      {text.split(/(\s+)/).map((part, index) => {
        if (!part.trim()) return part
        return (
          <span
            key={index}
            style={animationDelay(Math.round(word++ * step))}
            className={wordIn}
          >
            {part}
          </span>
        )
      })}
    </>
  )
}

const dot = css({
  width: '6px',
  height: '6px',
  borderRadius: 'full',
  backgroundColor: 'muted-foreground',
  opacity: 0.6,
  animation: 'typing_dot 1.2s ease-in-out infinite',
  _motionReduce: { animation: 'none' },
})

/** The assistant writing: three dots bouncing where its answer will appear. */
export function AssistantTypingDots() {
  const { t } = useTranslation('meetingAssistant')
  return (
    <div
      role="status"
      className={css({
        display: 'flex',
        alignItems: 'center',
        gap: '0.625rem',
      })}
    >
      <img
        src={ASSISTANT_ICON}
        alt=""
        aria-hidden="true"
        width={24}
        height={24}
      />
      <span
        aria-hidden="true"
        className={css({
          display: 'inline-flex',
          alignItems: 'center',
          gap: '4px',
          padding: '10px 12px',
          borderRadius: '14px 14px 14px 4px',
          backgroundColor: 'muted',
        })}
      >
        {[0, 150, 300].map((ms) => (
          <span key={ms} style={animationDelay(ms)} className={dot} />
        ))}
      </span>
      <span className={css({ srOnly: true })}>{t('pending')}</span>
    </div>
  )
}
