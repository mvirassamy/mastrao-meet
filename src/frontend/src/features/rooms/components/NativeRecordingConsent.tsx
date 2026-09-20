import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Button, H, Text } from '@/primitives'
import { HStack, VStack } from '@/styled-system/jsx'
import {
  decideNativeNotice,
  type NativeNoticeProjection,
} from '../api/nativeNotice'

export const NativeRecordingConsent = ({
  roomId,
  projection,
  onDecided,
}: {
  roomId: string
  projection: NativeNoticeProjection
  onDecided: () => Promise<void>
}) => {
  const { t, i18n } = useTranslation('rooms', { keyPrefix: 'nativeConsent' })
  const decision = useMutation({
    mutationFn: async (choice: 'accepted' | 'refused') => {
      await decideNativeNotice(roomId, choice, projection.notice)
      // Do not enter optimistically: the server must issue the next room projection.
      await onDecided()
    },
    retry: false,
  })
  return (
    <VStack gap="1rem" alignItems="center" textAlign="center">
      <H lvl={1} margin="sm" centered>
        {t('title', { defaultValue: 'Audio individuel pour la transcription' })}
      </H>
      <Text as="p">{projection.text}</Text>
      <Text as="p" variant="note">
        {t('retention', {
          defaultValue: 'Conservation prévue jusqu’au {{date}}.',
          date: new Intl.DateTimeFormat(i18n.language, {
            dateStyle: 'long',
          }).format(new Date(projection.notice.retention_expires_at * 1000)),
        })}
      </Text>
      <Text as="p" variant="note">
        {t('scope', {
          defaultValue:
            'Ce choix concerne votre piste audio individuelle. Il ne modifie pas votre choix précédent concernant l’enregistrement vidéo de la réunion.',
        })}
      </Text>
      {decision.isError && (
        <Text as="p" role="alert">
          {t('error', {
            defaultValue:
              'Votre choix n’a pas pu être confirmé. Réessayez ou rechargez la page pour vérifier son état.',
          })}
        </Text>
      )}
      <HStack gap="0.75rem" flexWrap="wrap" justifyContent="center">
        <Button
          variant="secondary"
          isDisabled={decision.isPending}
          onPress={() => decision.mutate('refused')}
        >
          {t('refuse', { defaultValue: 'Refuser cette capture audio' })}
        </Button>
        <Button
          isDisabled={decision.isPending}
          loading={decision.isPending}
          onPress={() => decision.mutate('accepted')}
        >
          {t('accept', { defaultValue: 'Accepter cette capture audio' })}
        </Button>
      </HStack>
    </VStack>
  )
}
