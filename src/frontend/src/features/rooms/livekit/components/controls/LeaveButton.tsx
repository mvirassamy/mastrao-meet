import { useState } from 'react'
import { useConnectionState, useRoomContext } from '@livekit/components-react'
import { Button, Dialog, DialogActions, P } from '@/primitives'
import { PhoneIcon } from '@/icons'
import { useTranslation } from 'react-i18next'
import { ConnectionState } from 'livekit-client'
import { reportError } from '@/features/analytics/telemetry'
import { navigateTo } from '@/navigation/navigateTo'
import {
  endMeeting,
  isRetryableEndMeetingError,
} from '@/features/rooms/api/endMeeting'
import { useMeetingLifecycle } from '@/features/rooms/contexts/MeetingLifecycleContext'

export const LeaveButton = ({
  roomId,
  canEnd = false,
  onEnded,
}: {
  roomId: string
  canEnd?: boolean
  onEnded?: () => void
}) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'controls' })
  const room = useRoomContext()
  const connectionState = useConnectionState(room)
  const [isOpen, setIsOpen] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [hasFailed, setHasFailed] = useState(false)
  const {
    phase,
    beginEnding,
    markActive,
    markEnding,
    markEndingUncertain,
    markEnded,
  } = useMeetingLifecycle()

  const leave = () => {
    setIsOpen(false)
    room.disconnect(true).catch((error) =>
      reportError('disconnect_failure', error, {
        context: 'An error occurred while disconnecting:',
      })
    )
    // An already disconnected Room emits no new disconnection event.
    if (connectionState === ConnectionState.Disconnected) {
      navigateTo('feedback', { outcome: 'left', roomId })
    }
  }

  const close = async () => {
    const closeRequestId = beginEnding()
    setIsSubmitting(true)
    setHasFailed(false)
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 15_000)
    try {
      const response = await endMeeting(
        roomId,
        closeRequestId,
        controller.signal
      )
      setIsOpen(false)
      if (response.state === 'ended') {
        markEnded()
        onEnded?.()
      } else {
        markEnding()
      }
    } catch (error) {
      if (isRetryableEndMeetingError(error)) {
        markEndingUncertain()
      } else {
        markActive()
      }
      setHasFailed(true)
    } finally {
      window.clearTimeout(timeout)
      setIsSubmitting(false)
    }
  }

  return (
    <>
      <Button
        shape="circle"
        variant="hangup"
        tooltip={t('leave')}
        aria-label={t('leave')}
        isDisabled={phase === 'requesting' || phase === 'ended'}
        onPress={() => (canEnd ? setIsOpen(true) : leave())}
        data-attr="controls-leave"
      >
        <PhoneIcon
          style={{
            transform: 'rotate(135deg)',
          }}
        />
      </Button>
      <Dialog
        isOpen={isOpen}
        role="alertdialog"
        title={t('endMeeting.dialog.title')}
        aria-label={t('endMeeting.dialog.title')}
      >
        <P>{t('endMeeting.dialog.body')}</P>
        {hasFailed && <P role="alert">{t('endMeeting.dialog.error')}</P>}
        <DialogActions>
          <Button
            variant="outline"
            isDisabled={isSubmitting}
            onPress={() => setIsOpen(false)}
          >
            {t('endMeeting.dialog.cancel')}
          </Button>
          <Button variant="outline" isDisabled={isSubmitting} onPress={leave}>
            {t('endMeeting.dialog.leave')}
          </Button>
          <Button
            variant="destructive"
            isDisabled={isSubmitting}
            onPress={close}
          >
            {isSubmitting
              ? t('endMeeting.dialog.ending')
              : t('endMeeting.dialog.confirm')}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  )
}
