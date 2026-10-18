import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Dialog, Modal, ModalOverlay } from 'react-aria-components'
import { AppAppearanceProvider } from '@/primitives/appAppearance'
import { css } from '@/styled-system/css'
import { ClassificationPalette } from './ClassificationPalette'
import { ConfirmStep, CreateStep } from './ClassificationSteps'

export type MatterDestination = { matter_ref: string; display_label: string }
export type DestinationSearch = {
  phase: 'idle' | 'loading' | 'ready' | 'error'
  items: MatterDestination[]
  hasMore: boolean
}

type Step =
  | { kind: 'choose' }
  | { kind: 'confirm'; matter: MatterDestination }
  | { kind: 'create'; label: string }

/**
 * Keyboard palette to file a meeting: choose (or name) a folder, then
 * confirm with the sharing notice in view.
 */
export function ClassificationDialog({
  isOpen,
  onClose,
  search,
  onSearch,
  onLoadMore,
  onClassify,
  onCreate,
  pending,
  error,
  createdMatter,
}: {
  isOpen: boolean
  onClose: () => void
  search: DestinationSearch
  onSearch: (query: string) => void
  onLoadMore: () => void
  onClassify: (matter: MatterDestination) => void
  onCreate: (label: string) => void
  pending: boolean
  error: 'conflict' | 'failed' | null
  createdMatter: MatterDestination | null
}) {
  const { t } = useTranslation('meetingHistory')
  const [step, setStep] = useState<Step>({ kind: 'choose' })
  const backToList = () => setStep({ kind: 'choose' })

  function renderStep() {
    // A folder created before a failed filing is resumed, never created twice.
    if (createdMatter)
      return (
        <ConfirmStep
          matter={createdMatter}
          resumed
          pending={pending}
          error={error}
          onConfirm={() => onClassify(createdMatter)}
          onClose={onClose}
        />
      )
    switch (step.kind) {
      case 'choose':
        return (
          <ClassificationPalette
            search={search}
            onSearch={onSearch}
            onLoadMore={onLoadMore}
            onChoose={(matter) => setStep({ kind: 'confirm', matter })}
            onCreate={(label) => setStep({ kind: 'create', label })}
            onClose={onClose}
          />
        )
      case 'confirm':
        return (
          <ConfirmStep
            matter={step.matter}
            resumed={false}
            pending={pending}
            error={error}
            onConfirm={() => onClassify(step.matter)}
            onBack={backToList}
            onClose={onClose}
          />
        )
      case 'create':
        return (
          <CreateStep
            initialLabel={step.label}
            pending={pending}
            error={error}
            onCreate={onCreate}
            onBack={backToList}
          />
        )
    }
  }

  return (
    <PaletteShell
      isOpen={isOpen}
      label={t('classification.title')}
      onOpenChange={(open) => {
        if (!open && !pending) onClose()
      }}
    >
      {renderStep()}
    </PaletteShell>
  )
}

/** Dialog placed in the upper third of the screen, like a command palette. */
function PaletteShell({
  isOpen,
  label,
  onOpenChange,
  children,
}: {
  isOpen: boolean
  label: string
  onOpenChange: (isOpen: boolean) => void
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
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'flex-start',
        padding: '1rem',
        paddingTop: { base: '1rem', md: '14vh' },
        backgroundColor: 'rgb(0 0 0 / 0.1)',
        backdropFilter: 'blur(4px)',
        transition: 'opacity 100ms ease',
        '&[data-entering], &[data-exiting]': { opacity: 0 },
        _motionReduce: { transition: 'none' },
      })}`}
    >
      <Modal
        className={css({
          width: '100%',
          maxWidth: '30rem',
          maxHeight: 'calc(100dvh - 2rem)',
          display: 'flex',
          overflow: 'hidden',
          borderRadius: '14px',
          backgroundColor: 'popover',
          color: 'popover-foreground',
          boxShadow:
            '0 0 0 1px rgb(8 20 46 / 0.08), 0 18px 40px rgb(8 20 46 / 0.16)',
          outline: 'none',
          transition: 'opacity 100ms ease, transform 100ms ease',
          '&[data-entering], &[data-exiting]': {
            opacity: 0,
            transform: 'scale(0.98)',
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
            fontSize: '0.875rem',
          })}
        >
          <AppAppearanceProvider>{children}</AppAppearanceProvider>
        </Dialog>
      </Modal>
    </ModalOverlay>
  )
}
