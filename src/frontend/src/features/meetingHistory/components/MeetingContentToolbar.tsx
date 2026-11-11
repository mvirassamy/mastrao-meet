import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckIcon, CopyIcon, DownloadIcon } from '@/icons'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import { useClipboardCopy } from './useClipboardCopy'

/** Saves the text shown in the tab as a plain-text file. */
const downloadText = (text: string, fileName: string) => {
  const url = URL.createObjectURL(
    new Blob([text], { type: 'text/plain;charset=utf-8' })
  )
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = fileName
  // Firefox only follows the click when the anchor is in the document, and
  // revoking the URL in the same tick cancels the download in some browsers.
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

/**
 * Quiet line above a summary or a transcript: a short note on the left and
 * a copy action, and optionally a download, on the right.
 */
export function MeetingContentToolbar({
  note,
  copyText,
  downloadName,
}: {
  note: ReactNode
  copyText: string
  /** File name offered to save the same text. */
  downloadName?: string
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
      <div className={css({ display: 'flex', gap: '0.25rem', flexShrink: 0 })}>
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
        {downloadName && (
          <Button
            variant="ghost"
            size="xs"
            icon={<DownloadIcon aria-hidden="true" />}
            onPress={() => downloadText(copyText, downloadName)}
          >
            {t('content.download')}
          </Button>
        )}
      </div>
      <span role="status" className={css({ srOnly: true })}>
        {copy !== 'idle' && t(`content.${copy}`)}
      </span>
    </div>
  )
}
