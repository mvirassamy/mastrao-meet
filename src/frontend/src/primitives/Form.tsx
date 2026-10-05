import { type FormEvent } from 'react'
import { Form as RACForm, type FormProps } from 'react-aria-components'
import { useTranslation } from 'react-i18next'
import { HStack } from '@/styled-system/jsx'
import { css } from '@/styled-system/css'
import { useCloseDialog } from './useCloseDialog'
import { type ButtonProps, Button } from './Button'
import { useAppAppearance } from './useAppAppearance'

/**
 * From wrapper that exposes form data on submit and adds submit/cancel buttons
 *
 * Wrap all your Fields in this component.
 * If the form is in a dialog, the cancel button closes the dialog unless you pass a custom onCancelButtonPress handler.
 */
export const Form = ({
  onSubmit,
  submitLabel,
  submitButtonProps,
  cancelButtonProps,
  withCancelButton = true,
  onCancelButtonPress,
  children,
  ...props
}: Omit<FormProps, 'onSubmit'> & {
  onSubmit?: (
    data: {
      [k: string]: FormDataEntryValue
    },
    event: FormEvent<HTMLFormElement>
  ) => void
  submitLabel: string
  submitButtonProps?: ButtonProps
  cancelButtonProps?: ButtonProps
  withCancelButton?: boolean
  onCancelButtonPress?: () => void
}) => {
  const { t } = useTranslation()
  const closeDialog = useCloseDialog()
  const isApp = useAppAppearance()
  const onCancel = withCancelButton
    ? onCancelButtonPress || closeDialog
    : undefined

  return (
    <RACForm
      {...props}
      onSubmit={(event) => {
        event.preventDefault()
        const formData = Object.fromEntries(new FormData(event.currentTarget))
        if (onSubmit) {
          onSubmit(formData, event)
        }
      }}
    >
      {children}
      {isApp ? (
        // Mastrao application actions: right-aligned, cancel before submit.
        <div
          className={css({
            display: 'flex',
            flexDirection: { base: 'column-reverse', sm: 'row' },
            justifyContent: 'flex-end',
            gap: '0.5rem',
            marginTop: '1rem',
          })}
        >
          {!!onCancel && (
            <Button
              variant="outline"
              {...cancelButtonProps}
              onPress={() => onCancel()}
            >
              {t('cancel')}
            </Button>
          )}
          <Button type="submit" variant="default" {...submitButtonProps}>
            {submitLabel}
          </Button>
        </div>
      ) : (
        <HStack gap="gutter">
          <Button type="submit" variant="default" {...submitButtonProps}>
            {submitLabel}
          </Button>
          {!!onCancel && (
            <Button
              variant="outline"
              {...cancelButtonProps}
              onPress={() => onCancel()}
            >
              {t('cancel')}
            </Button>
          )}
        </HStack>
      )}
    </RACForm>
  )
}
