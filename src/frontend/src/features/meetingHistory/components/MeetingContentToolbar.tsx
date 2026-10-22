import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckIcon, CopyIcon } from '@/icons'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import { useClipboardCopy } from './useClipboardCopy'

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
  const { copy, copyText: copyToClipboard } = useClipboardCopy()

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
        onPress={() => void copyToClipboard(copyText)}
      >
        {t(`content.${copy === 'idle' ? 'copy' : copy}`)}
      </Button>
      <span role="status" className={css({ srOnly: true })}>
        {copy !== 'idle' && t(`content.${copy}`)}
      </span>
    </div>
  )
}
