import React from 'react'
import { menuRecipe } from '@/primitives/menuRecipe'
import { HStack } from '@/styled-system/jsx'
import { CloseIcon } from '@/icons'
import { MenuItem } from 'react-aria-components'
import { useRemoveParticipant } from '@/features/rooms/api/removeParticipant'
import { useTranslation } from 'react-i18next'

type RemoveMenuItemProps = {
  identity: string
  displayedName?: string
}

export const RemoveMenuItem = React.memo(
  ({ identity, displayedName }: RemoveMenuItemProps) => {
    const { t } = useTranslation('rooms', {
      keyPrefix: 'participantMenu.remove',
    })
    const { removeParticipant } = useRemoveParticipant()
    return (
      <MenuItem
        aria-label={t('ariaLabel', { name: displayedName || identity })}
        className={menuRecipe({ icon: true, density: 'app' }).item}
        onAction={() => removeParticipant(identity)}
      >
        <HStack gap={0.25}>
          <CloseIcon size={20} aria-hidden />
          {t('label')}
        </HStack>
      </MenuItem>
    )
  }
)

RemoveMenuItem.displayName = 'RemoveMenuItem'
