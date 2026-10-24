import { createPortal } from 'react-dom'
import { CloseIcon, WarningIcon } from '@/icons'
import { Button } from '@/primitives'
import { css, cx } from '@/styled-system/css'

const toast = css({
  position: 'fixed',
  zIndex: 50,
  bottom: '1.5rem',
  left: '50%',
  transform: 'translateX(-50%)',
  display: 'flex',
  alignItems: 'center',
  gap: '0.75rem',
  width: 'max-content',
  maxWidth: 'calc(100vw - 2rem)',
  padding: '0.5rem 0.5rem 0.5rem 0.75rem',
  border: '1px solid token(colors.border)',
  borderLeftWidth: '3px',
  borderRadius: '12px',
  backgroundColor: 'popover',
  color: 'popover-foreground',
  fontSize: '0.875rem',
  boxShadow:
    'var(--shadow-strong) 0px 4px 8px 0px, var(--shadow-strong) 0px 6px 20px 4px',
  animation: 'fade 200ms',
})

const tones = {
  error: css({ borderLeftColor: 'danger' }),
  warning: css({ borderLeftColor: 'var(--call-attention-foreground)' }),
}

/** A toast at the bottom of the screen, kept while the problem lasts. */
export function NoteEnrichmentToast({
  tone,
  message,
  action,
}: {
  tone: 'error' | 'warning'
  message: string
  action?: { label: string; isDisabled?: boolean; onPress: () => void }
}) {
  return createPortal(
    <div className={cx(toast, tones[tone])}>
      {tone === 'error' ? (
        <span className={errorMark}>
          <CloseIcon aria-hidden="true" />
        </span>
      ) : (
        <WarningIcon
          aria-hidden="true"
          className={css({ color: 'var(--call-attention-foreground)' })}
        />
      )}
      <p role="alert" className={css({ fontWeight: 500 })}>
        {message}
      </p>
      {action && (
        <Button
          size="sm"
          isDisabled={action.isDisabled}
          onPress={action.onPress}
        >
          {action.label}
        </Button>
      )}
    </div>,
    document.body
  )
}
const errorMark = css({
  display: 'grid',
  placeItems: 'center',
  flexShrink: 0,
  width: '1.25rem',
  height: '1.25rem',
  borderRadius: 'full',
  backgroundColor: 'danger',
  color: 'white',
  '& svg': { width: '0.875rem', height: '0.875rem' },
})
