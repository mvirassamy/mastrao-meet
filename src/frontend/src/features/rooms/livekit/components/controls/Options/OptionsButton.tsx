import { useTranslation } from 'react-i18next'
import { RiMore2Fill } from '@remixicon/react'
import { Button, Menu } from '@/primitives'
import { OptionsMenuItems } from './OptionsMenuItems'

export const OptionsButton = () => {
  const { t } = useTranslation('rooms')

  return (
    <Menu variant="dark">
      <Button
        shape="circle"
        id="room-options-trigger"
        square
        variant="primaryDark"
        aria-label={t('options.buttonLabel')}
        tooltip={t('options.buttonLabel')}
      >
        <RiMore2Fill />
      </Button>
      <OptionsMenuItems />
    </Menu>
  )
}
