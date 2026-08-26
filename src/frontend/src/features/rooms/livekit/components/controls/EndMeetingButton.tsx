import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { RiStopCircleLine } from '@remixicon/react'

import { Button, Dialog, P } from '@/primitives'
import { HStack } from '@/styled-system/jsx'
import { endMeeting } from '@/features/rooms/api/endMeeting'
import { useMeetingLifecycle } from '@/features/rooms/contexts/MeetingLifecycleContext'

export const EndMeetingButton = ({
  roomId,
  description = false,
  onEnded,
}: {
  roomId: string
  description?: boolean
  onEnded?: () => void
}) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'controls.endMeeting' })
  const [isOpen, setIsOpen] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [hasFailed, setHasFailed] = useState(false)
  const { phase, beginEnding, markEnding, markEndingUncertain, markEnded } =
    useMeetingLifecycle()

  const confirm = async () => {
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
    } catch {
      markEndingUncertain()
      setHasFailed(true)
    } finally {
      window.clearTimeout(timeout)
      setIsSubmitting(false)
    }
  }

  return (
    <>
      <Button
        variant="danger"
        tooltip={t('label')}
        aria-label={t('label')}
        description={description}
        isDisabled={phase === 'requesting' || phase === 'ended'}
        onPress={() => setIsOpen(true)}
        data-attr="controls-end-meeting"
      >
        <RiStopCircleLine />
      </Button>
      <Dialog
        isOpen={isOpen}
        role="alertdialog"
        title={t('dialog.title')}
        aria-label={t('dialog.title')}
      >
        <P>{t('dialog.body')}</P>
        {hasFailed && <P role="alert">{t('dialog.error')}</P>}
        <HStack gap={2}>
          <Button
            variant="secondary"
            isDisabled={isSubmitting}
            onPress={() => setIsOpen(false)}
          >
            {t('dialog.cancel')}
          </Button>
          <Button variant="danger" isDisabled={isSubmitting} onPress={confirm}>
            {isSubmitting ? t('dialog.ending') : t('dialog.confirm')}
          </Button>
        </HStack>
      </Dialog>
    </>
  )
}
