import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useConfig } from '@/api/useConfig'
import { Button } from '@/primitives'
import { css, cva } from '@/styled-system/css'
import type { MeetingRecording } from '../api/types'
import { recordingAccessAction } from '../utils/recordingAccess'
import { VIDEO_ICON } from './meetingContent'

export const MeetingRecordingSection = ({
  recording,
  timeZone,
  align = 'end',
}: {
  recording: MeetingRecording
  timeZone?: string
  /**
   * Edge the notes follow once the header lays its actions beside the title:
   * 'end' when the section closes the header, 'start' when another action
   * sits on its right.
   */
  align?: 'start' | 'end'
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
      className={recordingSection({ align })}
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
            size="sm"
            icon={
              <img
                src={VIDEO_ICON}
                alt=""
                aria-hidden="true"
                width={22}
                height={22}
              />
            }
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
      <div
        className={recordingNotes({
          // Only a neighbouring action on the right leaves room for the
          // date to extend under it; alone, the section keeps its width.
          underNeighbour:
            Boolean(availableAction && access) && align === 'start',
        })}
      >
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
      </div>
    </section>
  )
}

const recordingSection = cva({
  base: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    gap: '0.25rem',
    flexShrink: 0,
    textAlign: 'left',
  },
  variants: {
    align: {
      start: {},
      // Same width as the header row of the meeting detail.
      end: {
        '@container meeting-detail (min-width: 46rem)': {
          alignItems: 'flex-end',
          textAlign: 'right',
        },
      },
    },
  },
})

/**
 * Notes under the download button. When another action sits on the right,
 * they take no width of their own: the date reads on one line, aligned on
 * the button's edge, and extends under that action instead of pushing it
 * away.
 */
const recordingNotes = cva({
  base: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'inherit',
    gap: '0.25rem',
  },
  variants: {
    underNeighbour: {
      true: { width: 0, minWidth: '100%', whiteSpace: 'nowrap' },
      false: {},
    },
  },
})
