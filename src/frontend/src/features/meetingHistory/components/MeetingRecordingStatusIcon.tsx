import { useTranslation } from 'react-i18next'
import { CheckCircleIcon, ErrorIcon, MinusCircleIcon, TimeIcon } from '@/icons'
import { VisualOnlyTooltip } from '@/primitives/VisualOnlyTooltip'
import { css } from '@/styled-system/css'
import type { MeetingRecordingStatus } from '../api/types'
import { STATUS_COLORS } from './meetingContent'

const recordingTone = (status: MeetingRecordingStatus) => {
  switch (status) {
    case 'available':
      return { Icon: CheckCircleIcon, color: STATUS_COLORS.ready }
    case 'processing':
    case 'unknown':
      return { Icon: TimeIcon, color: STATUS_COLORS.running }
    case 'failed':
      return { Icon: ErrorIcon, color: STATUS_COLORS.failed }
    case 'expired':
    case 'absent':
      return { Icon: MinusCircleIcon, color: STATUS_COLORS.absent, muted: true }
  }
}

/** The row link announces this status; the visual badge also has a shape. */
export const MeetingRecordingStatusIcon = ({
  status,
}: {
  status: MeetingRecordingStatus
}) => {
  const { t } = useTranslation('meetingHistory')
  const { Icon, color, muted } = recordingTone(status)
  return (
    <VisualOnlyTooltip tooltip={t(`status.recording.${status}`)}>
      <span
        aria-hidden="true"
        data-recording-status={status}
        className={css({
          position: 'relative',
          display: 'block',
          width: '34px',
          height: '34px',
        })}
      >
        <img
          src="/assets/illustrations/section-video.webp"
          alt=""
          width={34}
          height={34}
          decoding="async"
          className={css({
            display: 'block',
            userSelect: 'none',
            ...(muted && { filter: 'grayscale(1)', opacity: 0.4 }),
          })}
        />
        <Icon
          size={14}
          style={{ color }}
          className={css({
            position: 'absolute',
            right: '-3px',
            bottom: '-3px',
            borderRadius: '50%',
            backgroundColor: 'card',
            boxShadow: '0 0 0 1.5px token(colors.card)',
          })}
        />
      </span>
    </VisualOnlyTooltip>
  )
}
