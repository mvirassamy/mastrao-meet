import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useConfig } from '@/api/useConfig'
import { DownloadIcon } from '@/icons'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import type { MeetingRecording, MeetingRecordingStatus } from '../api/types'
import { recordingAccessAction } from '../utils/recordingAccess'
import { sectionCard, sectionHeader, sectionTitle } from './meetingDetailLayout'

/** Same tinted header as the summary and transcript sections. */
const recordingSectionLook = (status: MeetingRecordingStatus) => {
  switch (status) {
    case 'available':
      return 'content'
    case 'processing':
    case 'unknown':
      return 'pending'
    case 'failed':
      return 'failed'
    case 'absent':
    case 'expired':
      return 'empty'
  }
}

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
      className={`${sectionCard} ${css({
        marginBottom: { base: '1rem', md: '1.25rem' },
      })}`}
    >
      <div
        className={sectionHeader({
          look: recordingSectionLook(recording.status),
        })}
      >
        <img
          src="/assets/illustrations/section-video.webp"
          alt=""
          aria-hidden="true"
          width={96}
          height={96}
          decoding="async"
        />
        <h2 id="meeting-recording-heading" className={sectionTitle}>
          {t('recording.title')}
        </h2>
      </div>
      <div
        className={css({
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '0.75rem',
          padding: '1rem 1.125rem',
        })}
      >
        <div>
          <p
            ref={statusMessageRef}
            role="status"
            aria-live="polite"
            aria-atomic="true"
            tabIndex={-1}
            className={css({
              margin: 0,
              fontSize: '0.9375rem',
              fontWeight: 600,
              color: 'foreground',
            })}
          >
            {t(`status.recording.${recording.status}`)}
          </p>
          {recording.status === 'available' && retainedUntil && (
            <p
              className={css({
                margin: 0,
                fontSize: '0.8125rem',
                color: 'muted-foreground',
              })}
            >
              {t('recording.retainedUntil', {
                date: new Intl.DateTimeFormat(locale, {
                  dateStyle: 'long',
                  timeStyle: 'short',
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
                fontSize: '0.8125rem',
                color: 'muted-foreground',
              })}
            >
              {t('recording.accessUnavailable')}
            </p>
          )}
        </div>
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
            <input
              type="hidden"
              name="artifact_ref"
              value={access.artifactRef}
            />
            <input type="hidden" name="command_id" value={commandId} />
            <Button
              ref={actionButtonRef}
              type="submit"
              variant="outline"
              className={css({ minHeight: '44px' })}
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
      </div>
    </section>
  )
}
