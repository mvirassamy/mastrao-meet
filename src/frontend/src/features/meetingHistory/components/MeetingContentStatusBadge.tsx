import { CheckCircleIcon, ErrorIcon, MinusCircleIcon, TimeIcon } from '@/icons'
import { useTranslation } from 'react-i18next'
import { cva } from '@/styled-system/css'
import type { MeetingContentStatus } from '../api/types'

export type MeetingContentKind = 'summary' | 'transcript'

const badge = cva({
  base: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '0.25rem',
    minHeight: '22px',
    paddingX: '0.5rem',
    borderRadius: '999px',
    fontSize: '0.75rem',
    lineHeight: '1rem',
    fontWeight: 500,
    whiteSpace: 'nowrap',
    '& svg': { flexShrink: 0 },
  },
  variants: {
    status: {
      unknown: { backgroundColor: 'info', color: 'info-foreground' },
      not_started: { backgroundColor: 'muted', color: 'muted-foreground' },
      waiting_for_audio: { backgroundColor: 'info', color: 'info-foreground' },
      transcribing: { backgroundColor: 'info', color: 'info-foreground' },
      available: { backgroundColor: 'success', color: 'success-foreground' },
      completed_empty: {
        backgroundColor: 'muted',
        color: 'muted-foreground',
      },
      audio_unavailable: {
        backgroundColor: 'muted',
        color: 'muted-foreground',
      },
      partial: { backgroundColor: 'info', color: 'info-foreground' },
      failed: { backgroundColor: 'recording', color: 'recording-foreground' },
    },
  },
})

const icons = {
  unknown: TimeIcon,
  not_started: MinusCircleIcon,
  waiting_for_audio: TimeIcon,
  transcribing: TimeIcon,
  available: CheckCircleIcon,
  completed_empty: MinusCircleIcon,
  audio_unavailable: MinusCircleIcon,
  partial: TimeIcon,
  failed: ErrorIcon,
} as const

export const MeetingContentStatusBadge = ({
  kind,
  status,
}: {
  kind: MeetingContentKind
  status: MeetingContentStatus
}) => {
  const { t } = useTranslation('meetingHistory')
  const Icon = icons[status]

  return (
    <span className={badge({ status })} data-status={status}>
      <Icon size={13} aria-hidden="true" />
      {t(`status.${kind}.${status}`)}
    </span>
  )
}
