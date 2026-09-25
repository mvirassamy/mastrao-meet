import { useConnectionState, useRoomContext } from '@livekit/components-react'
import { Button } from '@/primitives'
import { RiPhoneFill } from '@remixicon/react'
import { useTranslation } from 'react-i18next'
import { ConnectionState } from 'livekit-client'
import { useParams } from 'wouter'
import { reportError } from '@/features/analytics/telemetry'
import { navigateTo } from '@/navigation/navigateTo'

export const LeaveButton = () => {
  const { t } = useTranslation('rooms', { keyPrefix: 'controls' })
  const room = useRoomContext()
  const connectionState = useConnectionState(room)
  // Feedback needs the room id to restore the cached platform return link.
  const { roomId } = useParams()
  return (
    <Button
      shape="circle"
      variant="destructive"
      tooltip={t('leave')}
      aria-label={t('leave')}
      onPress={() => {
        room.disconnect(true).catch((e) =>
          reportError('disconnect_failure', e, {
            context: 'An error occurred while disconnecting:',
          })
        )
        // An already disconnected Room emits no new disconnection event.
        if (connectionState === ConnectionState.Disconnected) {
          navigateTo('feedback', { outcome: 'left', roomId })
        }
      }}
      data-attr="controls-leave"
    >
      <RiPhoneFill
        style={{
          transform: 'rotate(135deg)',
        }}
      />
    </Button>
  )
}
