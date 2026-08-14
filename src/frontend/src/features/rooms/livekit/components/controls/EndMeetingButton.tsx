import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { RiStopCircleLine } from '@remixicon/react'

import { Button, Dialog, P } from '@/primitives'
import { HStack } from '@/styled-system/jsx'
import { endMeeting } from '@/features/rooms/api/endMeeting'
import { useMeetingLifecycle } from '@/features/rooms/contexts/MeetingLifecycleContext'

export const EndMeetingButton = ({ roomId }: { roomId: string }) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'controls.endMeeting' })
  const [isOpen, setIsOpen] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [hasFailed, setHasFailed] = useState(false)
  const { isEnding, markEnding } = useMeetingLifecycle()
  const closeRequestId = useRef(
    `close_${crypto.randomUUID().replaceAll('-', '')}`
  )

  const confirm = async () => {
    setIsSubmitting(true)
    setHasFailed(false)
    try {
      await endMeeting(roomId, closeRequestId.current)
      markEnding()
      setIsOpen(false)
    } catch {
      setHasFailed(true)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <>
      <Button
        variant="danger"
        tooltip={t('label')}
        aria-label={t('label')}
        isDisabled={isEnding}
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
        {hasFailed && <P>{t('dialog.error')}</P>}
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
