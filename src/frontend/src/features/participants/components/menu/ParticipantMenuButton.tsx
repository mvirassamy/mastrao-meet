import { Button, Menu } from '@/primitives'
import { MoreVerticalIcon } from '@/icons'
import { ParticipantMenu } from './ParticipantMenu'
import type { Participant } from 'livekit-client'
import { useTranslation } from 'react-i18next'

export const ParticipantMenuButton = ({
  participant,
}: {
  participant: Participant
}) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'participants' })
  return (
    <Menu density="app">
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={t('moreOptions')}
        tooltip={t('moreOptions')}
      >
        <MoreVerticalIcon />
      </Button>
      <ParticipantMenu participant={participant} />
    </Menu>
  )
}
