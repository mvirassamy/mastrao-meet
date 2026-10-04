import { useTranslation } from 'react-i18next'
import { DialogTrigger } from 'react-aria-components'
import { SettingsIcon } from '@/icons'
import { Button } from '@/primitives'
import type { ButtonProps } from '@/primitives'
import type { DialogProps } from '@/primitives'
import { SettingsDialog } from './SettingsDialog'

export const SettingsButton = ({
  buttonProps,
  dialogAppearance,
}: {
  buttonProps?: Pick<ButtonProps, 'size' | 'variant' | 'className'>
  dialogAppearance?: DialogProps['appearance']
} = {}) => {
  const { t } = useTranslation('settings')
  return (
    <DialogTrigger>
      <Button
        size="icon"
        variant="ghost"
        aria-label={t('settingsButtonLabel')}
        tooltip={t('settingsButtonLabel')}
        {...buttonProps}
      >
        <SettingsIcon />
      </Button>
      <SettingsDialog appearance={dialogAppearance} />
    </DialogTrigger>
  )
}
