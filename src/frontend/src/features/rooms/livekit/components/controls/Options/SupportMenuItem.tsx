import { QuestionIcon } from '@/icons'
import { MenuItem } from 'react-aria-components'
import { useTranslation } from 'react-i18next'
import { menuRecipe } from '@/primitives/menuRecipe'
import { Crisp } from 'crisp-sdk-web'
import { useIsSupportEnabled } from '@/features/support/hooks/useSupport'

export const SupportMenuItem = () => {
  const { t } = useTranslation('rooms', { keyPrefix: 'options.items' })
  const isSupportEnabled = useIsSupportEnabled()

  if (!isSupportEnabled || !Crisp) {
    return
  }

  return (
    <MenuItem
      className={
        menuRecipe({ icon: true, variant: 'dark', density: 'app' }).item
      }
      onAction={() => {
        Crisp?.chat.open()
      }}
    >
      <QuestionIcon size={20} />
      {t('support')}
    </MenuItem>
  )
}
