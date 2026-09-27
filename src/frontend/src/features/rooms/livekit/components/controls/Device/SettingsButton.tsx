import { Button } from '@/primitives'
import { SettingsIcon } from '@/icons'
import { useTranslation } from 'react-i18next'
import { SettingsDialogExtendedKey } from '@/features/settings/type'
import { openSettingsDialog } from '@/stores/settings'

export const SettingsButton = ({
  settingTab,
  onPress,
}: {
  settingTab: SettingsDialogExtendedKey
  onPress?: () => void
}) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'selectDevice' })

  return (
    <Button
      size="icon-lg"
      tooltip={t(`settings.${settingTab}`)}
      aria-label={t(`settings.${settingTab}`)}
      variant="ghost"
      onPress={() => {
        openSettingsDialog(settingTab)
        onPress?.()
      }}
    >
      <SettingsIcon aria-hidden="true" />
    </Button>
  )
}
