import type { ReactNode } from 'react'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'

/** Visual keyboard hint; the action it sits in already names itself. */
export const Kbd = ({ children }: { children: ReactNode }) => (
  <kbd
    aria-hidden="true"
    className={css({
      fontFamily: 'inherit',
      fontSize: '0.6875rem',
      fontWeight: 500,
      lineHeight: '1rem',
      paddingX: '0.3125rem',
      border: '1px solid token(colors.border)',
      borderBottomWidth: '2px',
      borderRadius: '5px',
      backgroundColor: 'card',
      color: 'muted-foreground',
    })}
  >
    {children}
  </kbd>
)

/** Grey bar at the bottom: the Escape action on the left, then the rest. */
export function PaletteFooter({
  escapeLabel,
  onEscape,
  isDisabled = false,
  children,
}: {
  escapeLabel: string
  onEscape: () => void
  /** Going back waits for a pending filing, like the other ways back. */
  isDisabled?: boolean
  children?: ReactNode
}) {
  return (
    <div
      className={css({
        display: 'flex',
        alignItems: 'center',
        gap: '0.75rem',
        padding: '0.5rem 0.75rem',
        borderTop: '1px solid token(colors.border)',
        backgroundColor: 'muted',
        color: 'muted-foreground',
        fontSize: '0.75rem',
      })}
    >
      <Button
        variant="ghost"
        size="xs"
        isDisabled={isDisabled}
        onPress={onEscape}
      >
        <Kbd>Esc</Kbd>
        {escapeLabel}
      </Button>
      {children}
    </div>
  )
}
