import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useConnectionState } from '@livekit/components-react'
import { ConnectionState } from 'livekit-client'
import { useTranslation } from 'react-i18next'
import { CloseIcon, RecordIcon } from '@/icons'
import { Button } from '@/primitives'
import { Spinner } from '@/primitives/Spinner'
import { css } from '@/styled-system/css'
import type { VideoRecordingPolicy } from '@/features/rooms/api/ApiRoom'
import {
  activateRecording,
  decideRecording,
} from '@/features/rooms/api/recordingConsent'
import { useMeetingLifecycle } from '@/features/rooms/contexts/MeetingLifecycleContext'
import { useSidePanel } from '../../hooks/useSidePanel'

type Action = 'start' | 'accepted' | 'refused'

export const RecordingPreparation = ({
  roomId,
  canStart,
  isHost,
  video,
  onRecordingChanged,
}: {
  roomId: string
  canStart?: boolean
  isHost?: boolean
  video: VideoRecordingPolicy
  onRecordingChanged?: () => Promise<VideoRecordingPolicy | undefined>
}) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'recordingConsent' })
  const { isEnding } = useMeetingLifecycle()
  const connectionState = useConnectionState()
  const [pending, setPending] = useState<Action | null>(null)
  const [activationAccepted, setActivationAccepted] = useState(false)
  const [failed, setFailed] = useState(false)
  const [dismissedNotice, setDismissedNotice] = useState<string | null>(null)
  const { isSidePanelOpen } = useSidePanel()
  const inFlight = useRef(false)
  const startRequestId = useRef(
    `activation_${crypto.randomUUID().replaceAll('-', '')}`
  )
  const locked = video.decision_lock !== 'open' || isEnding
  const startUnavailable =
    locked ||
    !canStart ||
    activationAccepted ||
    !video.start_available ||
    connectionState !== ConnectionState.Connected
  const showParticipantChoice =
    !isHost &&
    (video.start_requested || video.decision === 'refused') &&
    video.consultation_source === 'present' &&
    video.decision_lock === 'open'
  const showRefused = video.start_status === 'refused'
  const showHostPending =
    canStart === true &&
    pending !== 'start' &&
    !activationAccepted &&
    video.start_requested &&
    video.start_status === 'pending' &&
    video.decision_lock === 'open'
  const showStarting =
    pending === 'start' ||
    activationAccepted ||
    video.decision_lock === 'start_in_progress'

  useEffect(() => {
    if (video.decision_lock !== 'open') {
      setActivationAccepted(false)
    }
  }, [video.decision_lock])

  const act = async (action: Action) => {
    if (locked || inFlight.current) return
    if (action === 'start' && startUnavailable) return
    inFlight.current = true
    setPending(action)
    setFailed(false)
    try {
      if (action === 'start') {
        await activateRecording(roomId, startRequestId.current)
        setActivationAccepted(true)
        startRequestId.current = `activation_${crypto.randomUUID().replaceAll('-', '')}`
        const refreshedVideo = await onRecordingChanged?.()
        if (
          refreshedVideo?.decision_lock === 'open' &&
          refreshedVideo.start_status === 'pending'
        ) {
          setActivationAccepted(false)
        }
      } else {
        await decideRecording(
          roomId,
          action,
          `decision_${crypto.randomUUID().replaceAll('-', '')}`
        )
        await onRecordingChanged?.()
      }
    } catch {
      setFailed(true)
      await onRecordingChanged?.().catch(() => undefined)
    } finally {
      inFlight.current = false
      setPending(null)
    }
  }

  const notice = recordingNotice({
    showStarting,
    showParticipantChoice,
    showRefused,
    showHostPending,
  })
  const content = notice ? noticeContent(notice, video.decision) : null
  const noticeKey = notice ? `${notice}:${video.decision}` : null
  // A closed notice comes back as soon as the situation changes.
  const [shownKey, setShownKey] = useState(noticeKey)
  if (noticeKey !== shownKey) {
    setShownKey(noticeKey)
    setDismissedNotice(null)
  }
  const isDismissed = noticeKey !== null && noticeKey === dismissedNotice
  const showCard = (content || failed) && !isDismissed

  return (
    <>
      {/* Always mounted, so screen readers announce each new notice. */}
      <p role="status" className={css({ srOnly: true })}>
        {content ? t(content.title) : ''}
      </p>
      {canStart && (
        <Button
          shape="circle"
          variant="outline"
          aria-label={t('startVideo')}
          tooltip={t('startVideo')}
          isDisabled={startUnavailable || pending !== null}
          loading={pending === 'start'}
          onPress={() => act('start')}
        >
          <RecordIcon />
        </Button>
      )}
      {/* A participant who closed their refusal can still change their mind. */}
      {notice === 'choice' && isDismissed && (
        <Button
          shape="circle"
          variant="outline"
          aria-label={t('changeAnswer')}
          tooltip={t('changeAnswer')}
          onPress={() => setDismissedNotice(null)}
        >
          <RecordIcon />
        </Button>
      )}
      {showCard && (
        <RecordingNoticeCard
          title={content ? t(content.title) : null}
          body={content?.body ? t(content.body) : null}
          muted={content?.muted ?? false}
          busy={content?.busy ?? false}
          error={failed ? t('videoActionError') : null}
          onDismiss={
            content?.dismissible ? () => setDismissedNotice(noticeKey) : null
          }
          dismissLabel={t('dismiss')}
          besideSidePanel={isSidePanelOpen}
        >
          {notice === 'choice' && (
            <div className={css({ display: 'flex', gap: '0.5rem' })}>
              <Button
                size="sm"
                variant={video.decision === 'refused' ? 'default' : 'outline'}
                aria-pressed={video.decision === 'refused'}
                isDisabled={locked || pending !== null}
                loading={pending === 'refused'}
                onPress={() => act('refused')}
              >
                {t('videoNo')}
              </Button>
              <Button
                size="sm"
                variant={video.decision === 'refused' ? 'outline' : 'default'}
                aria-pressed={video.decision === 'accepted'}
                isDisabled={locked || pending !== null}
                loading={pending === 'accepted'}
                onPress={() => act('accepted')}
              >
                {t('videoYes')}
              </Button>
            </div>
          )}
        </RecordingNoticeCard>
      )}
    </>
  )
}

type Notice = 'starting' | 'choice' | 'refused' | 'pending'

/** One notice at a time, the most urgent first. */
const recordingNotice = (state: {
  showStarting: boolean
  showParticipantChoice: boolean
  showRefused: boolean
  showHostPending: boolean
}): Notice | null => {
  if (state.showStarting) return 'starting'
  if (state.showParticipantChoice) return 'choice'
  if (state.showRefused) return 'refused'
  if (state.showHostPending) return 'pending'
  return null
}

type NoticeContent = {
  title: string
  body: string | null
  muted: boolean
  busy: boolean
  dismissible: boolean
}

/** Translation keys and look of each notice; lasting notices can be closed. */
const noticeContent = (
  notice: Notice,
  decision: VideoRecordingPolicy['decision']
): NoticeContent => {
  switch (notice) {
    case 'starting':
      return {
        title: 'starting',
        body: null,
        muted: false,
        busy: true,
        dismissible: false,
      }
    case 'pending':
      return {
        title: 'videoPending',
        body: 'videoPendingBody',
        muted: false,
        busy: false,
        dismissible: true,
      }
    case 'refused':
      return {
        title: 'videoRefused',
        body: 'videoRefusedBody',
        muted: true,
        busy: false,
        dismissible: true,
      }
    case 'choice':
      return choiceContent(decision)
  }
}

const choiceContent = (
  decision: VideoRecordingPolicy['decision']
): NoticeContent => {
  const base = { body: 'videoQuestionBody', muted: false, busy: false }
  switch (decision) {
    case 'absent':
      return { ...base, title: 'videoQuestion', dismissible: false }
    case 'accepted':
      return { ...base, title: 'videoAccepted', dismissible: true }
    case 'refused':
      return { ...base, title: 'videoDeclined', dismissible: true }
  }
}

/**
 * Floating card above the call controls, kept beside an open side panel. On
 * narrow screens the panel covers the video and the card sits over it, so
 * every lasting notice can be closed; the question itself ends with an answer.
 */
const RecordingNoticeCard = ({
  title,
  body,
  muted,
  busy,
  error,
  onDismiss,
  dismissLabel,
  besideSidePanel,
  children,
}: {
  title: string | null
  body: string | null
  muted: boolean
  busy: boolean
  error: string | null
  onDismiss: (() => void) | null
  dismissLabel: string
  besideSidePanel: boolean
  children?: ReactNode
}) => (
  <section
    data-beside-panel={besideSidePanel}
    className={css({
      position: 'absolute',
      bottom: 'calc(100% + 0.5rem)',
      left: '1rem',
      right: '1rem',
      '&[data-beside-panel=true]': {
        // Same breakpoint as the room layout: below it the panel covers the video.
        right: {
          base: '1rem',
          sm: 'calc(var(--sizes-room-side-panel) + var(--sizes-room-side-panel-margin) + 1rem)',
        },
      },
      zIndex: 10,
      display: 'flex',
      flexWrap: 'wrap',
      alignItems: 'center',
      gap: '0.75rem',
      maxWidth: '32rem',
      marginX: 'auto',
      padding: '0.75rem 0.875rem',
      border: '1px solid token(colors.border)',
      borderRadius: '14px',
      backgroundColor: 'card',
      boxShadow: '0 18px 40px -20px rgb(8 20 46 / 0.45)',
      textAlign: 'left',
    })}
  >
    <img
      src="/assets/illustrations/section-video.webp"
      alt=""
      width={40}
      height={40}
      className={css({
        flexShrink: 0,
        ...(muted && { filter: 'grayscale(1)', opacity: 0.5 }),
      })}
    />
    <div className={css({ flex: '1 1 14rem', minWidth: 0 })}>
      {title && (
        <div
          className={css({
            display: 'flex',
            alignItems: 'center',
            gap: '0.5rem',
          })}
        >
          <p
            className={css({
              margin: 0,
              fontSize: '0.875rem',
              fontWeight: 600,
              color: 'foreground',
            })}
          >
            {title}
          </p>
          {busy && <Spinner size={14} />}
        </div>
      )}
      {body && (
        <p
          className={css({
            margin: '0.125rem 0 0',
            fontSize: '0.75rem',
            lineHeight: 1.45,
            color: 'muted-foreground',
          })}
        >
          {body}
        </p>
      )}
      {error && (
        <p
          role="alert"
          className={css({
            margin: '0.25rem 0 0',
            fontSize: '0.75rem',
            color: 'danger',
          })}
        >
          {error}
        </p>
      )}
    </div>
    {children}
    {onDismiss && (
      <Button
        size="icon-sm"
        variant="ghost"
        aria-label={dismissLabel}
        tooltip={dismissLabel}
        onPress={onDismiss}
      >
        <CloseIcon />
      </Button>
    )}
  </section>
)
