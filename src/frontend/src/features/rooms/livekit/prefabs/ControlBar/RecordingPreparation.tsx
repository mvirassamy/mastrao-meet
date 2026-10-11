import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useConnectionState } from '@livekit/components-react'
import { ConnectionState } from 'livekit-client'
import { useTranslation } from 'react-i18next'
import { RecordIcon } from '@/icons'
import { Button } from '@/primitives'
import { Spinner } from '@/primitives/Spinner'
import { css } from '@/styled-system/css'
import type { VideoRecordingPolicy } from '@/features/rooms/api/ApiRoom'
import {
  activateRecording,
  decideRecording,
} from '@/features/rooms/api/recordingConsent'
import { useMeetingLifecycle } from '@/features/rooms/contexts/MeetingLifecycleContext'

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

  const copy = notice ? NOTICE_COPY[notice] : null

  if (!notice && !canStart && !failed) return null

  return (
    <>
      {canStart && (
        <Button
          variant="outline"
          size="sm"
          icon={<RecordIcon aria-hidden="true" />}
          isDisabled={startUnavailable || pending !== null}
          loading={pending === 'start'}
          onPress={() => act('start')}
        >
          {t('startVideo')}
        </Button>
      )}
      {(notice || failed) && (
        <RecordingNoticeCard
          title={copy ? t(copy.title(video.decision)) : null}
          body={copy?.body ? t(copy.body) : null}
          muted={notice === 'refused'}
          busy={notice === 'starting'}
          error={failed ? t('videoActionError') : null}
        >
          {notice === 'choice' && (
            <div className={css({ display: 'flex', gap: '0.5rem' })}>
              {(['refused', 'accepted'] as const).map((choice) => (
                <Button
                  key={choice}
                  size="sm"
                  variant={
                    choice === primaryChoice(video.decision)
                      ? 'default'
                      : 'outline'
                  }
                  aria-pressed={video.decision === choice}
                  isDisabled={locked || pending !== null}
                  loading={pending === choice}
                  onPress={() => act(choice)}
                >
                  {t(choice === 'accepted' ? 'videoYes' : 'videoNo')}
                </Button>
              ))}
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

type Decision = VideoRecordingPolicy['decision']

const CHOICE_TITLES: Record<Decision, string> = {
  absent: 'videoQuestion',
  accepted: 'videoAccepted',
  refused: 'videoDeclined',
}

const NOTICE_COPY: Record<
  Notice,
  { title: (decision: Decision) => string; body: string | null }
> = {
  starting: { title: () => 'starting', body: null },
  choice: {
    title: (decision) => CHOICE_TITLES[decision],
    body: 'videoQuestionBody',
  },
  refused: { title: () => 'videoRefused', body: 'videoRefusedBody' },
  pending: { title: () => 'videoPending', body: 'videoPendingBody' },
}

/** The participant's answer stands out; Accept leads before any answer. */
const primaryChoice = (decision: Decision) =>
  decision === 'refused' ? 'refused' : 'accepted'

/** Floating card centred above the call controls. */
const RecordingNoticeCard = ({
  title,
  body,
  muted,
  busy,
  error,
  children,
}: {
  title: string | null
  body: string | null
  muted: boolean
  busy: boolean
  error: string | null
  children?: ReactNode
}) => (
  <section
    className={css({
      position: 'absolute',
      bottom: 'calc(100% + 0.5rem)',
      left: '50%',
      transform: 'translateX(-50%)',
      zIndex: 10,
      display: 'flex',
      flexWrap: 'wrap',
      alignItems: 'center',
      gap: '0.75rem',
      width: 'min(32rem, calc(100vw - 2rem))',
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
            role="status"
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
  </section>
)
