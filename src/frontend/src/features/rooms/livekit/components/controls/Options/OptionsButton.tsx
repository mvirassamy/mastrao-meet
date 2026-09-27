import { useTranslation } from 'react-i18next'
import { MoreVerticalIcon } from '@/icons'
import { Button, Menu } from '@/primitives'
import { OptionsMenuItems } from './OptionsMenuItems'

export const OptionsButton = () => {
  const { t } = useTranslation('rooms')

  return (
    <Menu variant="dark" density="app">
      <Button
        shape="circle"
        id="room-options-trigger"
        variant="outline"
        aria-label={t('options.buttonLabel')}
        tooltip={t('options.buttonLabel')}
      >
        <MoreVerticalIcon />
      </Button>
      <OptionsMenuItems />
    </Menu>
  )
}
