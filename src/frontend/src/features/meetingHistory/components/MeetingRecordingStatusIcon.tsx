import { useTranslation } from 'react-i18next'
import {
  CheckCircleIcon,
  ErrorIcon,
  MinusCircleIcon,
  TimeIcon,
  VideoIcon,
} from '@/icons'
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
      return { Icon: MinusCircleIcon, color: STATUS_COLORS.absent }
  }
}

/** The row link announces this status; the visual badge also has a shape. */
export const MeetingRecordingStatusIcon = ({
  status,
}: {
  status: MeetingRecordingStatus
}) => {
  const { t } = useTranslation('meetingHistory')
  const { Icon, color } = recordingTone(status)
  return (
    <VisualOnlyTooltip tooltip={t(`status.recording.${status}`)}>
      <span
        aria-hidden="true"
        data-recording-status={status}
        className={css({
          position: 'relative',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: '34px',
          height: '34px',
          color: 'muted-foreground',
        })}
      >
        <VideoIcon size={26} />
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
