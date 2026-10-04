import type { RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'wouter'
import { css } from '@/styled-system/css'
import type { MeetingHistoryItem } from '../api/types'
import { meetingHistoryDetailPath } from '../paths'
import { rememberOpenedMeeting } from '../utils/focusReturn'

/**
 * Title link of a meeting row or event: its overlay makes the whole row
 * clickable, and it reads the statuses that the icons only show.
 */
export const MeetingRowLink = ({
  item,
  linkRef,
}: {
  item: MeetingHistoryItem
  linkRef: RefObject<HTMLAnchorElement>
}) => {
  const { t } = useTranslation('meetingHistory')

  return (
    <Link
      ref={linkRef}
      to={meetingHistoryDetailPath(item.id)}
      data-meeting-id={item.id}
      onClick={() => rememberOpenedMeeting(item.id)}
      className={css({
        display: 'block',
        overflow: 'hidden',
        color: 'foreground',
        fontSize: '0.9375rem',
        lineHeight: '1.375rem',
        fontWeight: 500,
        textDecoration: 'none',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        outline: 'none',
        _after: { content: '""', position: 'absolute', inset: 0 },
        '&:focus-visible::after': {
          outline: '2px solid token(colors.ring)',
          outlineOffset: '-2px',
          borderRadius: '11px',
        },
      })}
    >
      {item.title ?? t('untitled')}
      <span className={css({ srOnly: true })}>
        {t(`status.summary.${item.summaryStatus}`)}
      </span>
      <span className={css({ srOnly: true })}>
        {t(`status.transcript.${item.transcriptStatus}`)}
      </span>
    </Link>
  )
}
