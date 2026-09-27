import { useTranslation } from 'react-i18next'
import { DialogTrigger } from 'react-aria-components'
import { RiSettings3Fill } from '@remixicon/react'
import { Button } from '@/primitives'
import type { ButtonProps } from '@/primitives'
import type { DialogProps } from '@/primitives'
import { SettingsDialog } from './SettingsDialog'

export const SettingsButton = ({
  buttonProps,
  dialogAppearance,
}: {
  buttonProps?: Pick<ButtonProps, 'size' | 'className'>
  dialogAppearance?: DialogProps['appearance']
} = {}) => {
  const { t } = useTranslation('settings')
  return (
    <DialogTrigger>
      <Button
        square
        variant="secondaryText"
        aria-label={t('settingsButtonLabel')}
        tooltip={t('settingsButtonLabel')}
        {...buttonProps}
      >
        <RiSettings3Fill />
      </Button>
      <SettingsDialog appearance={dialogAppearance} />
    </DialogTrigger>
  )
}
