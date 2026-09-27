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
      available: { backgroundColor: 'success', color: 'success-foreground' },
      processing: { backgroundColor: 'info', color: 'info-foreground' },
      absent: { backgroundColor: 'muted', color: 'muted-foreground' },
      failed: { backgroundColor: 'recording', color: 'recording-foreground' },
    },
  },
})

const icons = {
  available: CheckCircleIcon,
  processing: TimeIcon,
  absent: MinusCircleIcon,
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
