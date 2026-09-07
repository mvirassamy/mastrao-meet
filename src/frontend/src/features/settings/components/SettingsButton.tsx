import { useTranslation } from 'react-i18next'
import { DialogTrigger } from 'react-aria-components'
import { RiSettings3Fill } from '@remixicon/react'
import { Button } from '@/primitives'
import { SettingsDialog } from './SettingsDialog'

export const SettingsButton = () => {
  const { t } = useTranslation('settings')
  return (
    <DialogTrigger>
      <Button
        square
        variant="secondaryText"
        aria-label={t('settingsButtonLabel')}
        tooltip={t('settingsButtonLabel')}
      >
        <RiSettings3Fill />
      </Button>
      <SettingsDialog />
    </DialogTrigger>
  )
}
