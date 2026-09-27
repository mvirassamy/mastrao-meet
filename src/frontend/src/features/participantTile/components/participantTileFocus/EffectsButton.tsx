import React from 'react'
import { useTranslation } from 'react-i18next'
import { useSidePanel } from '@/features/rooms/livekit/hooks/useSidePanel'
import { Button } from '@/primitives'
import { EffectsIcon } from '@/icons'
export const EffectsButton = React.memo(() => {
  const { t } = useTranslation('rooms', { keyPrefix: 'participantTileFocus' })
  const { isEffectsOpen, toggleEffects } = useSidePanel()
  return (
    <Button
      size="icon-sm"
      variant={'ghost'}
      tooltip={t('effects')}
      onPress={() => !isEffectsOpen && toggleEffects()}
    >
      <EffectsIcon />
    </Button>
  )
})

EffectsButton.displayName = 'EffectsButton'
