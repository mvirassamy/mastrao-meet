import { css } from '@/styled-system/css'

const firstCharacter = (value: string): string => {
  if (typeof Intl !== 'undefined' && 'Segmenter' in Intl) {
    const [first] = new Intl.Segmenter(undefined, {
      granularity: 'grapheme',
    }).segment(value)
    return first?.segment ?? ''
  }
  return Array.from(value)[0] ?? ''
}

const getUserInitials = (name: string): string => {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return ''
  if (words.length === 1 && words[0].includes('@')) {
    return firstCharacter(words[0]).toLocaleUpperCase()
  }
  return words.slice(0, 2).map(firstCharacter).join('').toLocaleUpperCase()
}

type MeetUserAvatarProps = {
  name: string
  compact?: boolean
}

/** Platform-style avatar: rounded square, muted surface, centered initials. */
export const MeetUserAvatar = ({
  name,
  compact = false,
}: MeetUserAvatarProps) => (
  <span
    aria-hidden="true"
    className={css({
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      flexShrink: 0,
      width: compact ? '24px' : '32px',
      height: compact ? '24px' : '32px',
      borderRadius: compact ? '6px' : '8px',
      border: '1px solid var(--border)',
      backgroundColor: 'var(--muted)',
      color: 'var(--muted-foreground)',
      fontSize: compact ? '0.6875rem' : '0.8125rem',
      fontWeight: 500,
      lineHeight: 1,
      letterSpacing: 0,
      userSelect: 'none',
    })}
  >
    {getUserInitials(name)}
  </span>
)
