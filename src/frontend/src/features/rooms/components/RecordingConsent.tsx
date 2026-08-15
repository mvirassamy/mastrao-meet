import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, H, Text } from '@/primitives'
import { HStack, VStack } from '@/styled-system/jsx'
import {
  decideRecording,
  stopRecording,
  type RecordingDecision,
} from '../api/recordingConsent'

export const RecordingConsent = ({
  roomId,
  retentionExpiresAt,
  onDecided,
}: {
  roomId: string
  retentionExpiresAt: number
  onDecided: () => Promise<void>
}) => {
  const { t, i18n } = useTranslation('rooms', {
    keyPrefix: 'recordingConsent',
  })
  const requestIds = useRef<Record<RecordingDecision, string>>({
    accepted: `consent_${crypto.randomUUID().replaceAll('-', '')}`,
    refused: `refusal_${crypto.randomUUID().replaceAll('-', '')}`,
    withdrawn: `withdrawal_${crypto.randomUUID().replaceAll('-', '')}`,
  })
  const [pending, setPending] = useState<RecordingDecision | null>(null)
  const [failed, setFailed] = useState(false)
  const refusalStopId = useRef(
    `stop_${crypto.randomUUID().replaceAll('-', '')}`
  )

  const decide = async (decision: 'accepted' | 'refused') => {
    setPending(decision)
    setFailed(false)
    try {
      await decideRecording(roomId, decision, requestIds.current[decision])
      if (decision === 'refused') {
        await stopRecording(roomId, 'refusal', refusalStopId.current)
      }
      await onDecided()
    } catch {
      setFailed(true)
    } finally {
      setPending(null)
    }
  }

  return (
    <VStack gap="1rem" alignItems="center" textAlign="center">
      <H lvl={1} margin="sm" centered>
        {t('title')}
      </H>
      <Text as="p">{t('notice')}</Text>
      <Text as="p" variant="note">
        {t('retention', {
          date: new Intl.DateTimeFormat(i18n.language, {
            dateStyle: 'long',
          }).format(new Date(retentionExpiresAt * 1000)),
        })}
      </Text>
      {failed && (
        <Text as="p" role="alert">
          {t('error')}
        </Text>
      )}
      <HStack gap="0.75rem">
        <Button
          variant="secondary"
          isDisabled={pending !== null}
          onPress={() => decide('refused')}
        >
          {t('refuse')}
        </Button>
        <Button
          isDisabled={pending !== null}
          loading={pending === 'accepted'}
          onPress={() => decide('accepted')}
        >
          {t('accept')}
        </Button>
      </HStack>
    </VStack>
  )
}
