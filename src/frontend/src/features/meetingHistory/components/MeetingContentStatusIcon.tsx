import { useTranslation } from 'react-i18next'
import { css, cva } from '@/styled-system/css'
import { VisualOnlyTooltip } from '@/primitives/VisualOnlyTooltip'
import type { MeetingContentStatus } from '../api/types'
import { SECTION_ICONS, type MeetingContentKind } from './meetingContent'

type StatusTone = 'ready' | 'running' | 'failed' | 'absent'

const statusTone = (status: MeetingContentStatus): StatusTone => {
  switch (status) {
    case 'available':
      return 'ready'
    case 'failed':
      return 'failed'
    case 'not_started':
    case 'completed_empty':
    case 'audio_unavailable':
      return 'absent'
    case 'unknown':
    case 'waiting_for_audio':
    case 'transcribing':
    case 'partial':
      return 'running'
  }
}

const dot = cva({
  base: {
    position: 'absolute',
    right: '-1px',
    bottom: '-1px',
    width: '11px',
    height: '11px',
    borderRadius: '50%',
    boxShadow: '0 0 0 2px token(colors.card)',
  },
  variants: {
    tone: {
      ready: { backgroundColor: '#2f9e5a' },
      running: { backgroundColor: 'primary' },
      failed: { backgroundColor: '#c4323d' },
      absent: { backgroundColor: '#98a2b3' },
    },
  },
})

/**
 * Illustrated status of a meeting content (summary or transcript): the 3D
 * icon, greyed out when the content is absent, with a coloured status dot.
 * Decorative: the exact status is a hover tooltip; screen readers get it
 * from the row link.
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
        <span className={dot({ tone })} />
      </span>
    </VisualOnlyTooltip>
  )
}
