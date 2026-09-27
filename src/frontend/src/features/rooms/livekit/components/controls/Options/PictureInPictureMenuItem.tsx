import { PictureInPictureIcon } from '@/icons'
import { MenuItem } from 'react-aria-components'
import { useTranslation } from 'react-i18next'
import { menuRecipe } from '@/primitives/menuRecipe'
import { usePictureInPicture } from '@/features/pip/hooks/usePictureInPicture'

export const PictureInPictureMenuItem = () => {
  const { t } = useTranslation('rooms', { keyPrefix: 'options.items' })
  const { toggle, isOpen, isSupported } = usePictureInPicture()

  if (!isSupported) return null

  return (
    <MenuItem
      className={
        menuRecipe({ icon: true, variant: 'dark', density: 'app' }).item
      }
      onAction={toggle}
    >
      <PictureInPictureIcon size={20} />
      {t(`pictureInPicture.${isOpen ? 'exit' : 'enter'}`)}
    </MenuItem>
  )
}
