import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, H, Text } from '@/primitives'
import { Checkbox } from '@/primitives/Checkbox'
import { HStack, VStack } from '@/styled-system/jsx'
import {
  decideRecording,
  decideTranscription,
  type RecordingDecision,
} from '../api/recordingConsent'

export const RecordingConsent = ({
  roomId,
  retentionExpiresAt,
  participantKind,
  transcriptionOffered,
  recordingDecision,
  transcriptionDecision,
  onDecided,
}: {
  roomId: string
  retentionExpiresAt: number
  participantKind?: 'host' | 'guest'
  transcriptionOffered?: boolean
  recordingDecision?: 'absent' | 'accepted' | 'refused' | 'withdrawn'
  transcriptionDecision?: 'absent' | 'accepted' | 'refused' | 'withdrawn'
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
  const transcriptionRequestIds = useRef<Record<RecordingDecision, string>>({
    accepted: `consent_${crypto.randomUUID().replaceAll('-', '')}`,
    refused: `refusal_${crypto.randomUUID().replaceAll('-', '')}`,
    withdrawn: `withdrawal_${crypto.randomUUID().replaceAll('-', '')}`,
  })
  const [pending, setPending] = useState<RecordingDecision | null>(null)
  const [failed, setFailed] = useState(false)
  const [transcriptionAccepted, setTranscriptionAccepted] = useState(false)
  const transcriptionOnly =
    recordingDecision === 'accepted' &&
    transcriptionOffered &&
    transcriptionDecision === 'absent'

  const decide = async (decision: 'accepted' | 'refused') => {
    setPending(decision)
    setFailed(false)
    try {
      if (
        transcriptionOffered &&
        transcriptionDecision === 'absent' &&
        decision === 'refused'
      ) {
        await decideTranscription(
          roomId,
          'refused',
          transcriptionRequestIds.current.refused
        )
      }
      if (recordingDecision === 'absent') {
        await decideRecording(roomId, decision, requestIds.current[decision])
      }
      if (
        transcriptionOffered &&
        transcriptionDecision === 'absent' &&
        decision === 'accepted'
      ) {
        const transcriptionDecision =
          transcriptionOnly || transcriptionAccepted ? 'accepted' : 'refused'
        await decideTranscription(
          roomId,
          transcriptionDecision,
          transcriptionRequestIds.current[transcriptionDecision]
        )
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
        {t(transcriptionOnly ? 'transcription.title' : 'title')}
      </H>
      {transcriptionOnly ? (
        <Text as="p">{t('transcription.pendingNotice')}</Text>
      ) : (
        <>
          <Text as="p">{t('notice')}</Text>
          <Text as="p">{t('purpose')}</Text>
          <Text as="p">{t('recipients')}</Text>
        </>
      )}
      <Text as="p" variant="note">
        {t('retention', {
          date: new Intl.DateTimeFormat(i18n.language, {
            dateStyle: 'long',
          }).format(new Date(retentionExpiresAt * 1000)),
        })}
      </Text>
      <Text as="p" variant="note">
        {t('rights')}
      </Text>
      {participantKind === 'guest' && (
        <Text as="p" variant="note">
          {t('guestIdentity')}
        </Text>
      )}
      {transcriptionOffered && !transcriptionOnly && (
        <VStack gap="0.25rem" alignItems="flex-start" textAlign="left">
          <Checkbox
            isSelected={transcriptionAccepted}
            onChange={setTranscriptionAccepted}
          >
            {t('transcription.checkbox')}
          </Checkbox>
          <Text as="p" variant="note">
            {t('transcription.notice')}
          </Text>
        </VStack>
      )}
      {failed && (
        <Text as="p" role="alert">
          {t('error')}
        </Text>
      )}
      <HStack gap="0.75rem" flexWrap="wrap" justifyContent="center">
        <Button
          variant="secondary"
          isDisabled={pending !== null}
          onPress={() => decide('refused')}
        >
          {t(transcriptionOnly ? 'transcription.refuse' : 'refuse')}
        </Button>
        <Button
          isDisabled={pending !== null}
          loading={pending === 'accepted'}
          onPress={() => decide('accepted')}
        >
          {t(transcriptionOnly ? 'transcription.accept' : 'accept')}
        </Button>
      </HStack>
    </VStack>
  )
}
