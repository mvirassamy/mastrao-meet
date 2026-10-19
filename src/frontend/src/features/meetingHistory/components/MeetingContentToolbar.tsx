import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckIcon, CopyIcon } from '@/icons'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'

type CopyState = 'idle' | 'copied' | 'failed'

const COPY_FEEDBACK_MS = 2000

/**
 * Quiet line above a summary or a transcript: a short note on the left and
 * a copy action on the right.
 */
export function MeetingContentToolbar({
  note,
  copyText,
}: {
  note: ReactNode
  copyText: string
}) {
  const { t } = useTranslation('meetingHistory')
  const [copy, setCopy] = useState<CopyState>('idle')

  useEffect(() => {
    if (copy === 'idle') return
    const timeout = window.setTimeout(() => setCopy('idle'), COPY_FEEDBACK_MS)
    return () => window.clearTimeout(timeout)
  }, [copy])

  async function copyContent() {
    try {
      await navigator.clipboard.writeText(copyText)
      setCopy('copied')
    } catch {
      setCopy('failed')
    }
  }

  return (
    <div
      className={css({
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '0.75rem',
        marginBottom: '0.75rem',
      })}
    >
      <p
        className={css({
          display: 'flex',
          alignItems: 'flex-start',
          gap: '0.375rem',
          margin: 0,
          color: 'muted-foreground',
          fontSize: '0.8125rem',
          lineHeight: '1.25rem',
          '& svg': { flexShrink: 0, marginTop: '0.125rem' },
        })}
      >
        {note}
      </p>
      <Button
        variant="ghost"
        size="xs"
        icon={
          copy === 'copied' ? (
            <CheckIcon aria-hidden="true" />
          ) : (
            <CopyIcon aria-hidden="true" />
          )
        }
        onPress={() => void copyContent()}
      >
        {t(`content.${copy === 'idle' ? 'copy' : copy}`)}
      </Button>
      <span role="status" className={css({ srOnly: true })}>
        {copy !== 'idle' && t(`content.${copy}`)}
      </span>
    </div>
  )
}
