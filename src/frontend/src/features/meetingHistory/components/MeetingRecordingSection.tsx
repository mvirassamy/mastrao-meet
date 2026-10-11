import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useConfig } from '@/api/useConfig'
import { DownloadIcon } from '@/icons'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import type { MeetingRecording } from '../api/types'
import { recordingAccessAction } from '../utils/recordingAccess'

export const MeetingRecordingSection = ({
  recording,
  timeZone,
}: {
  recording: MeetingRecording
  timeZone?: string
}) => {
  const { t, i18n } = useTranslation('meetingHistory')
  const { data: config } = useConfig()
  const [commandId] = useState(
    () => `recording_access_${crypto.randomUUID().replaceAll('-', '')}`
  )
  const retainedUntil = recording.retentionExpiresAt
  const [retentionExpired, setRetentionExpired] = useState(
    () => retainedUntil !== null && retainedUntil.getTime() <= Date.now()
  )
  const actionButtonRef = useRef<HTMLButtonElement>(null)
  const actionHasFocus = useRef(false)
  const statusMessageRef = useRef<HTMLParagraphElement>(null)
  const accessUnavailableMessageRef = useRef<HTMLParagraphElement>(null)
  const restoreFocusAfterExpiry = useRef(false)
  useEffect(() => {
    if (!retainedUntil) {
      setRetentionExpired(false)
      return
    }
    let timeout: number | undefined
    const checkRetention = () => {
      const remaining = retainedUntil.getTime() - Date.now()
      if (remaining <= 0) {
        restoreFocusAfterExpiry.current = actionHasFocus.current
        setRetentionExpired(true)
        return
      }
      setRetentionExpired(false)
      timeout = window.setTimeout(
        checkRetention,
        Math.min(remaining + 1, 2_147_483_647)
      )
    }
    checkRetention()
    return () => window.clearTimeout(timeout)
  }, [retainedUntil])
  const action = recordingAccessAction(
    recording,
    config?.mastrao_platform_origin
  )
  const access = recording.access
  const locale = i18n.resolvedLanguage || i18n.language
  const availableAction = retentionExpired ? null : action
  useLayoutEffect(() => {
    if (!availableAction) return
    return () => {
      restoreFocusAfterExpiry.current = actionHasFocus.current
    }
  }, [availableAction])
  useLayoutEffect(() => {
    if (!availableAction && restoreFocusAfterExpiry.current) {
      const focusTarget =
        accessUnavailableMessageRef.current ?? statusMessageRef.current
      focusTarget?.focus()
      restoreFocusAfterExpiry.current = false
    }
  }, [availableAction])

  return (
    <section
      aria-labelledby="meeting-recording-heading"
      data-section="recording"
      className={css({
        display: 'flex',
        flexDirection: 'column',
        alignItems: { base: 'flex-start', sm: 'flex-end' },
        gap: '0.25rem',
        flexShrink: 0,
        textAlign: { base: 'left', sm: 'right' },
      })}
    >
      <h2 id="meeting-recording-heading" className={css({ srOnly: true })}>
        {t('recording.title')}
      </h2>
      {availableAction && access && (
        <form
          method="post"
          action={availableAction}
          target="_blank"
          rel="noopener"
        >
          <input type="hidden" name="matter_ref" value={access.matterRef} />
          <input type="hidden" name="meeting_ref" value={access.meetingRef} />
          <input
            type="hidden"
            name="recording_ref"
            value={access.recordingRef}
          />
          <input type="hidden" name="artifact_ref" value={access.artifactRef} />
          <input type="hidden" name="command_id" value={commandId} />
          <Button
            ref={actionButtonRef}
            type="submit"
            variant="outline"
            icon={<DownloadIcon aria-hidden="true" />}
            onFocus={() => {
              actionHasFocus.current = true
            }}
            onBlur={() => {
              actionHasFocus.current = false
            }}
          >
            {t('recording.open')}
          </Button>
        </form>
      )}
      {/* The button already says the video is available. */}
      <p
        ref={statusMessageRef}
        role="status"
        aria-live="polite"
        aria-atomic="true"
        tabIndex={-1}
        className={
          availableAction
            ? css({ srOnly: true })
            : css({
                margin: 0,
                fontSize: '0.8125rem',
                color: 'muted-foreground',
              })
        }
      >
        {t(`status.recording.${recording.status}`)}
      </p>
      {recording.status === 'available' && retainedUntil && (
        <p
          className={css({
            margin: 0,
            fontSize: '0.75rem',
            color: 'muted-foreground',
          })}
        >
          {t('recording.retainedUntil', {
            date: new Intl.DateTimeFormat(locale, {
              dateStyle: 'long',
              timeZone,
            }).format(retainedUntil),
          })}
        </p>
      )}
      {recording.status === 'available' && !availableAction && (
        <p
          ref={accessUnavailableMessageRef}
          role="status"
          aria-live="polite"
          tabIndex={-1}
          className={css({
            margin: 0,
            fontSize: '0.75rem',
            color: 'muted-foreground',
          })}
        >
          {t('recording.accessUnavailable')}
        </p>
      )}
    </section>
  )
}
