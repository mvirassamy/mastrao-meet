import { type ReactNode } from 'react'
import { type DialogProps as RACDialogProps } from 'react-aria-components'
import { AppDialog } from './AppDialog'

export type DialogProps = Omit<RACDialogProps, 'children'> & {
  title?: string
  children: ReactNode | ((opts: { close: () => void }) => ReactNode)
  onClose?: () => void
  /**
   * use the Dialog as a controlled component
   */
  isOpen?: boolean
  /**
   * use the Dialog as a controlled component:
   * this is called when isOpen should be updated
   * after user interaction
   */
  onOpenChange?: (isOpen: boolean) => void
  /** Kept for existing call sites; the layout no longer depends on it. */
  type?: 'flex' | 'alert'
  /** 'full' (default) fits forms and alerts, 'wide' illustrated content. */
  size?: 'full' | 'wide' | 'large'
  /**
   * 'app' is the authenticated workspace (home, history): Platform backdrop
   * with blur. Elsewhere, including rooms, the backdrop is a plain dim layer:
   * a blur would be recomputed on every frame of the live video behind it.
   */
  appearance?: 'default' | 'app'
}

/**
 * Application dialog. Every dialog shares the Mastrao AppDialog standard.
 */
export const Dialog = ({
  title,
  children,
  onClose,
  isOpen,
  onOpenChange,
  size = 'full',
  appearance = 'default',
  type: _type,
  ...dialogProps
}: DialogProps) => {
  void _type
  const isApp = appearance === 'app'
  return (
    <AppDialog
      {...dialogProps}
      title={title || undefined}
      size={
        size === 'large' ? (isApp ? 'lg' : 'xl') : size === 'wide' ? 'lg' : 'md'
      }
      backdrop={isApp ? 'blur' : 'dim'}
      isOpen={isOpen}
      onOpenChange={(open) => {
        onOpenChange?.(open)
        if (!open) onClose?.()
      }}
    >
      {children}
    </AppDialog>
  )
}
