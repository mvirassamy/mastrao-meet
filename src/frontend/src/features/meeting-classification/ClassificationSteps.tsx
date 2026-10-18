import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useKeyboard } from 'react-aria'
import { ArrowLeftIcon, GroupIcon, WarningIcon } from '@/icons'
import { Button, Field } from '@/primitives'
import { css } from '@/styled-system/css'
import type { MatterDestination } from './ClassificationDialog'
import { DOSSIER_ICON } from './ClassificationStatus'

export const Kbd = ({ children }: { children: ReactNode }) => (
  <kbd
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
  children,
}: {
  escapeLabel: string
  onEscape: () => void
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
      <button
        type="button"
        onClick={onEscape}
        className={css({
          display: 'inline-flex',
          alignItems: 'center',
          gap: '0.375rem',
          padding: '0.25rem',
          border: 'none',
          borderRadius: '6px',
          background: 'none',
          color: 'inherit',
          font: 'inherit',
          cursor: 'pointer',
          _hover: { color: 'foreground' },
          _focusVisible: { outline: '2px solid token(colors.ring)' },
        })}
      >
        <Kbd>Esc</Kbd>
        {escapeLabel}
      </button>
      {children}
    </div>
  )
}

/**
 * Escape goes back to the folder list instead of closing the dialog, so a
 * second Escape closes it.
 */
const useEscapeBack = (onBack: (() => void) | undefined, pending: boolean) =>
  useKeyboard({
    onKeyDown: (event) => {
      if (event.key !== 'Escape' || !onBack) {
        event.continuePropagation()
        return
      }
      event.preventDefault()
      if (!pending) onBack()
    },
  }).keyboardProps

function StepHeader({
  title,
  onBack,
  pending,
}: {
  title: string
  onBack?: () => void
  pending: boolean
}) {
  const { t } = useTranslation('meetingHistory')
  return (
    <div
      className={css({
        display: 'flex',
        alignItems: 'center',
        gap: '0.5rem',
        padding: '0.75rem 1rem',
        borderBottom: '1px solid token(colors.border)',
      })}
    >
      {onBack && (
        <Button
          size="icon"
          variant="ghost"
          aria-label={t('classification.back')}
          isDisabled={pending}
          onPress={onBack}
        >
          <ArrowLeftIcon aria-hidden="true" />
        </Button>
      )}
      <h2
        className={css({
          margin: 0,
          fontSize: '0.9375rem',
          lineHeight: '1.5rem',
          fontWeight: 600,
          color: 'var(--heading-foreground)',
        })}
      >
        {title}
      </h2>
    </div>
  )
}

const SharingNote = () => {
  const { t } = useTranslation('meetingHistory')
  return (
    <p
      className={css({
        display: 'flex',
        gap: '0.5rem',
        margin: 0,
        padding: '0.625rem 0.75rem',
        borderRadius: '10px',
        backgroundColor: 'muted',
        color: 'muted-foreground',
        fontSize: '0.8125rem',
        lineHeight: '1.25rem',
        '& svg': { flexShrink: 0, marginTop: '0.125rem' },
      })}
    >
      <GroupIcon size={15} aria-hidden="true" />
      {t('classification.sharing')}
    </p>
  )
}

const FilingError = ({ error }: { error: 'conflict' | 'failed' | null }) => {
  const { t } = useTranslation('meetingHistory')
  if (!error) return null
  return (
    <p
      role="alert"
      className={css({
        display: 'flex',
        gap: '0.5rem',
        margin: 0,
        color: 'destructive',
        fontSize: '0.8125rem',
        lineHeight: '1.25rem',
        '& svg': { flexShrink: 0, marginTop: '0.125rem' },
      })}
    >
      <WarningIcon size={15} aria-hidden="true" />
      {t(`classification.${error}`)}
    </p>
  )
}

const stepBody = css({
  display: 'flex',
  flexDirection: 'column',
  gap: '0.75rem',
  padding: '1rem',
})

export function ConfirmStep({
  matter,
  resumed,
  pending,
  error,
  onConfirm,
  onBack,
  onClose,
}: {
  matter: MatterDestination
  /** The folder was created; only the filing remains to confirm. */
  resumed: boolean
  pending: boolean
  error: 'conflict' | 'failed' | null
  onConfirm: () => void
  onBack?: () => void
  onClose: () => void
}) {
  const { t } = useTranslation('meetingHistory')
  const escapeProps = useEscapeBack(onBack, pending)
  return (
    <div {...escapeProps}>
      <StepHeader
        title={t('classification.confirmTitle')}
        onBack={onBack}
        pending={pending}
      />
      <div className={stepBody}>
        <div
          className={css({
            display: 'flex',
            alignItems: 'center',
            gap: '0.625rem',
            padding: '0.625rem 0.75rem',
            border: '1px solid token(colors.border)',
            borderRadius: '11px',
          })}
        >
          <img src={DOSSIER_ICON} alt="" width={28} height={28} />
          <strong
            className={css({
              flex: 1,
              minWidth: 0,
              overflowWrap: 'anywhere',
              fontWeight: 600,
              color: 'var(--heading-foreground)',
            })}
          >
            {matter.display_label}
          </strong>
          {onBack && (
            <Button
              variant="link"
              size="xs"
              isDisabled={pending}
              onPress={onBack}
            >
              {t('classification.change')}
            </Button>
          )}
        </div>
        {resumed && (
          <p role="status" className={css({ margin: 0 })}>
            {t('classification.created', { name: matter.display_label })}
          </p>
        )}
        <SharingNote />
        <FilingError error={error} />
      </div>
      <PaletteFooter
        escapeLabel={t(onBack ? 'classification.back' : 'classification.close')}
        onEscape={onBack ?? onClose}
      >
        <Button
          size="sm"
          // The dialog opens this step on its one action: Enter confirms.
          // eslint-disable-next-line jsx-a11y/no-autofocus
          autoFocus
          isDisabled={pending}
          loading={pending}
          onPress={onConfirm}
          className={css({ marginLeft: 'auto' })}
        >
          {t(resumed ? 'classification.resume' : 'classification.classifyHere')}
        </Button>
      </PaletteFooter>
    </div>
  )
}

export function CreateStep({
  initialLabel,
  pending,
  error,
  onCreate,
  onBack,
}: {
  initialLabel: string
  pending: boolean
  error: 'conflict' | 'failed' | null
  onCreate: (label: string) => void
  onBack: () => void
}) {
  const { t } = useTranslation('meetingHistory')
  const [label, setLabel] = useState(initialLabel)
  const name = label.trim()
  const escapeProps = useEscapeBack(onBack, pending)
  return (
    <form
      {...escapeProps}
      onSubmit={(event) => {
        event.preventDefault()
        if (name && !pending) onCreate(name)
      }}
    >
      <StepHeader
        title={t('classification.newTitle')}
        onBack={onBack}
        pending={pending}
      />
      <div className={stepBody}>
        <Field
          type="text"
          label={t('classification.name')}
          value={label}
          onChange={setLabel}
          maxLength={200}
          isRequired
          // The name is the only thing to fill in this step.
          // eslint-disable-next-line jsx-a11y/no-autofocus
          autoFocus
          isDisabled={pending}
        />
        <SharingNote />
        <FilingError error={error} />
      </div>
      <PaletteFooter escapeLabel={t('classification.back')} onEscape={onBack}>
        <Button
          type="submit"
          size="sm"
          isDisabled={pending || !name}
          loading={pending}
          className={css({ marginLeft: 'auto' })}
        >
          {t('classification.createAndClassify')}
        </Button>
      </PaletteFooter>
    </form>
  )
}
