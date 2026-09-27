import { type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Dialog as RACDialog,
  Heading,
  Modal,
  ModalOverlay,
  type DialogProps as RACDialogProps,
} from 'react-aria-components'
import { RiCloseFill } from '@remixicon/react'
import { css, cva, cx } from '@/styled-system/css'
import { Button } from './Button'
import { AppAppearanceProvider } from './appAppearance'

type RenderProp = ReactNode | ((opts: { close: () => void }) => ReactNode)

export type AppDialogProps = Omit<RACDialogProps, 'children'> & {
  title: string
  description?: ReactNode
  children: RenderProp
  /** Actions shown in the muted footer, aligned to the right on desktop. */
  footer?: RenderProp
  size?: 'sm' | 'md' | 'lg'
  /** Controlled mode; omit inside a DialogTrigger. */
  isOpen?: boolean
  onOpenChange?: (isOpen: boolean) => void
  /** Extra classes for the body, e.g. a fixed height for tabbed content. */
  bodyClassName?: string
}

const overlay = css({
  position: 'fixed',
  inset: 0,
  zIndex: 1000,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: '1rem',
  backgroundColor: 'rgb(0 0 0 / 0.1)',
  backdropFilter: 'blur(4px)',
  transition: 'opacity 100ms ease',
  '&[data-entering], &[data-exiting]': { opacity: 0 },
  _motionReduce: { transition: 'none' },
})

const panel = cva({
  base: {
    position: 'relative',
    display: 'flex',
    flexDirection: 'column',
    width: '100%',
    maxHeight: 'calc(100dvh - 2rem)',
    overflow: 'hidden',
    borderRadius: '12px',
    backgroundColor: 'popover',
    color: 'popover-foreground',
    boxShadow: '0 0 0 1px rgb(8 20 46 / 0.1)',
    outline: 'none',
    transition: 'opacity 100ms ease, transform 100ms ease',
    '&[data-entering], &[data-exiting]': {
      opacity: 0,
      transform: 'scale(0.95)',
    },
    _motionReduce: { transition: 'none' },
  },
  variants: {
    size: {
      sm: { maxWidth: '24rem' },
      md: { maxWidth: '32rem' },
      lg: { maxWidth: '46rem' },
    },
  },
})

const dialog = css({
  display: 'flex',
  flexDirection: 'column',
  minHeight: 0,
  flex: 1,
  outline: 'none',
  fontSize: '0.875rem',
  lineHeight: '1.25rem',
})

/**
 * Mastrao application dialog: the Platform dialog translated to React Aria.
 * Scoped to the authenticated workspace palette; room dialogs keep Dialog.
 */
export const AppDialog = ({
  title,
  description,
  children,
  footer,
  size = 'sm',
  isOpen,
  onOpenChange,
  bodyClassName,
  ...dialogProps
}: AppDialogProps) => {
  const { t } = useTranslation()
  const isAlert = dialogProps.role === 'alertdialog'
  const render = (value: RenderProp | undefined, close: () => void) =>
    typeof value === 'function' ? value({ close }) : value

  return (
    <ModalOverlay
      isOpen={isOpen}
      onOpenChange={onOpenChange}
      isDismissable={!isAlert}
      isKeyboardDismissDisabled={isAlert}
      className={`authenticated-meet-workspace ${overlay}`}
    >
      <Modal className={panel({ size })}>
        <RACDialog {...dialogProps} className={dialog}>
          {({ close }) => (
            <AppAppearanceProvider>
              <div
                className={css({
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '0.5rem',
                  padding: '1rem',
                  paddingRight: isAlert ? '1rem' : '3rem',
                })}
              >
                <Heading
                  slot="title"
                  className={css({
                    margin: 0,
                    fontSize: '1rem',
                    lineHeight: 1,
                    fontWeight: 500,
                  })}
                >
                  {title}
                </Heading>
                {description && (
                  <p
                    className={css({
                      margin: 0,
                      color: 'muted-foreground',
                      fontSize: '0.875rem',
                      lineHeight: '1.25rem',
                    })}
                  >
                    {description}
                  </p>
                )}
              </div>
              <div
                className={cx(
                  css({
                    minHeight: 0,
                    flex: 1,
                    overflowY: 'auto',
                    paddingX: '1rem',
                    paddingBottom: '1rem',
                    // Normalise the typography of reused content.
                    '& :is(h2, h3)': {
                      fontSize: '0.875rem',
                      lineHeight: '1.25rem',
                      fontWeight: 600,
                    },
                    '& p': { fontSize: '0.875rem', lineHeight: '1.25rem' },
                  }),
                  bodyClassName
                )}
              >
                {render(children, close)}
              </div>
              {footer && (
                <div
                  className={css({
                    display: 'flex',
                    flexDirection: { base: 'column-reverse', sm: 'row' },
                    justifyContent: 'flex-end',
                    gap: '0.5rem',
                    padding: '1rem',
                    borderTop: '1px solid token(colors.border)',
                    backgroundColor:
                      'color-mix(in srgb, var(--muted) 50%, transparent)',
                  })}
                >
                  {render(footer, close)}
                </div>
              )}
              {!isAlert && (
                <Button
                  square
                  size="appIcon"
                  variant="ghost"
                  aria-label={t('closeDialog')}
                  onPress={close}
                  className={css({
                    position: 'absolute',
                    top: '0.5rem',
                    right: '0.5rem',
                    minWidth: { base: '44px', md: '32px' },
                    minHeight: { base: '44px', md: '32px' },
                    color: 'muted-foreground',
                    _hover: { color: 'foreground' },
                  })}
                >
                  <RiCloseFill aria-hidden="true" />
                </Button>
              )}
            </AppAppearanceProvider>
          )}
        </RACDialog>
      </Modal>
    </ModalOverlay>
  )
}
