import { useTranslation } from 'react-i18next'
import { Avatar } from '@/components/Avatar'
import { AccountBoxIcon } from '@/icons'
import { css } from '@/styled-system/css'
import { VisualOnlyTooltip } from '@/primitives/VisualOnlyTooltip'

const MAX_BUBBLES = 3
const AVATAR_COLORS = ['#2d5be3', '#1d9660', '#c2410c']

/** Overlapping bubbles leave room for one letter: keep the first name only. */
const firstName = (name: string) => name.trim().split(/\s+/)[0]

const bubble = css({
  display: 'grid',
  placeItems: 'center',
  width: '30px',
  height: '30px',
  borderRadius: '50%',
  overflow: 'hidden',
  boxShadow: '0 0 0 2px token(colors.card)',
  '&:not(:first-child)': { marginLeft: '-6px' },
})

/**
 * Participants as overlapping avatar bubbles: up to three people, or two
 * people and a "+N" bubble. A participant whose name the server did not send
 * shows a person icon instead of initials.
 */
export const MeetingParticipantsStack = ({
  count,
  names,
}: {
  count: number | null
  names: string[]
}) => {
  const { t } = useTranslation('meetingHistory')
  const total = Math.max(count ?? 0, names.length)
  if (total === 0) return null

  const label = t('participants', { count: total })
  const shown = total > MAX_BUBBLES ? MAX_BUBBLES - 1 : total
  const hidden = total - shown

  return (
    <>
      <VisualOnlyTooltip tooltip={label}>
        <span aria-hidden="true" className={css({ display: 'flex' })}>
          {AVATAR_COLORS.slice(0, shown).map((color, index) => (
            <span
              key={color}
              className={bubble}
              style={{ backgroundColor: color, color: '#ffffff' }}
            >
              {names[index] ? (
                <Avatar
                  name={firstName(names[index])}
                  context="placeholder"
                  style={{ cursor: 'inherit' }}
                />
              ) : (
                <AccountBoxIcon size={14} />
              )}
            </span>
          ))}
          {hidden > 0 && (
            <span
              className={`${bubble} ${css({
                backgroundColor: 'muted',
                color: 'muted-foreground',
                fontSize: '0.6875rem',
                fontWeight: 600,
              })}`}
            >
              +{hidden}
            </span>
          )}
        </span>
      </VisualOnlyTooltip>
      <span className={css({ srOnly: true })}>{label}</span>
    </>
  )
}
