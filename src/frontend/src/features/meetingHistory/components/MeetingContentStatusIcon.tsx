import type { RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircleIcon, ErrorIcon, MinusCircleIcon, TimeIcon } from '@/icons'
import { css } from '@/styled-system/css'
import { VisualOnlyTooltip } from '@/primitives/VisualOnlyTooltip'
import type { MeetingContentStatus, MeetingHistoryItem } from '../api/types'
import {
  contentPhase,
  SECTION_ICONS,
  STATUS_COLORS,
  type MeetingContentKind,
} from './meetingContent'
import { RowOverlayCell } from './RowOverlayCell'
import { MeetingRecordingStatusIcon } from './MeetingRecordingStatusIcon'

type StatusTone = keyof typeof STATUS_COLORS

const statusTone = (status: MeetingContentStatus): StatusTone => {
  switch (contentPhase(status)) {
    case 'ready':
      return 'ready'
    case 'partial':
    case 'pending':
      return 'running'
    case 'failed':
      return 'failed'
    case 'absent':
      return 'absent'
  }
}

/** Each tone has its own shape, so the status never relies on colour. */
const TONE_ICONS = {
  ready: CheckCircleIcon,
  running: TimeIcon,
  failed: ErrorIcon,
  absent: MinusCircleIcon,
} as const

/**
 * Illustrated status of a meeting content (summary or transcript): the 3D
 * icon, greyed out when the content is absent, with a status badge.
 * Decorative: the exact status is a hover tooltip; screen readers get it
 * from the meeting link.
 */
export const MeetingContentStatusIcon = ({
  kind,
  status,
}: {
  kind: MeetingContentKind
  status: MeetingContentStatus
}) => {
  const { t } = useTranslation('meetingHistory')
  const tone = statusTone(status)
  const BadgeIcon = TONE_ICONS[tone]

  return (
    <VisualOnlyTooltip tooltip={t(`status.${kind}.${status}`)}>
      <span
        aria-hidden="true"
        data-status={status}
        className={css({
          position: 'relative',
          display: 'block',
          width: '34px',
          height: '34px',
        })}
      >
        <img
          src={SECTION_ICONS[kind]}
          alt=""
          width={34}
          height={34}
          decoding="async"
          className={css({
            display: 'block',
            userSelect: 'none',
            ...(tone === 'absent' && { filter: 'grayscale(1)', opacity: 0.4 }),
          })}
        />
        <BadgeIcon
          size={14}
          style={{ color: STATUS_COLORS[tone] }}
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

/** Summary and transcript icons of a meeting row, clickable like the row. */
export const MeetingContentStatusIcons = ({
  item,
  linkRef,
}: {
  item: MeetingHistoryItem
  linkRef: RefObject<HTMLAnchorElement>
}) => (
  <RowOverlayCell linkRef={linkRef}>
    <MeetingContentStatusIcon kind="summary" status={item.summaryStatus} />
    <MeetingContentStatusIcon
      kind="transcript"
      status={item.transcriptStatus}
    />
    {item.recordingProjected === true && (
      <MeetingRecordingStatusIcon status={item.recordingStatus} />
    )}
  </RowOverlayCell>
)
