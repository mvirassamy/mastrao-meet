import { type ReactNode } from 'react'
import { Dialog, Modal, ModalOverlay } from 'react-aria-components'
import { AppAppearanceProvider } from '@/primitives/appAppearance'
import { css } from '@/styled-system/css'

/**
 * The assistant over the page on narrower screens: a panel sliding from the
 * right, full screen on a phone.
 */
export function MeetingAssistantDrawer({
  isOpen,
  onOpenChange,
  label,
  children,
}: {
  isOpen: boolean
  onOpenChange: (isOpen: boolean) => void
  label: string
  children: ReactNode
}) {
  return (
    <ModalOverlay
      isOpen={isOpen}
      onOpenChange={onOpenChange}
      isDismissable
      className={`authenticated-meet-workspace ${css({
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        backgroundColor: 'rgb(8 20 46 / 0.35)',
        transition: 'opacity 150ms ease',
        '&[data-entering], &[data-exiting]': { opacity: 0 },
        _motionReduce: { transition: 'none' },
      })}`}
    >
      <Modal
        className={css({
          position: 'fixed',
          top: 0,
          right: 0,
          bottom: 0,
          display: 'flex',
          width: { base: '100%', md: '26rem' },
          backgroundColor: 'card',
          boxShadow: '-12px 0 32px rgb(8 20 46 / 0.12)',
          outline: 'none',
          transition: 'transform 200ms ease',
          '&[data-entering], &[data-exiting]': {
            transform: 'translateX(100%)',
          },
          _motionReduce: { transition: 'none' },
        })}
      >
        <Dialog
          aria-label={label}
          className={css({
            display: 'flex',
            flexDirection: 'column',
            flex: 1,
            minWidth: 0,
            outline: 'none',
          })}
        >
          <AppAppearanceProvider>{children}</AppAppearanceProvider>
        </Dialog>
      </Modal>
    </ModalOverlay>
  )
}
