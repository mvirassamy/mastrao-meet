import { useTranslation } from 'react-i18next'
import { Text } from '@/primitives'

export const ManagedTranscriptionNotice = ({
  profileRef,
}: {
  profileRef?: 'mistral-eu-standard-managed-demo-v1'
}) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'nativeConsent' })
  if (!profileRef) return null

  return (
    <Text as="p" variant="note">
      {t('managedProviderNotice')}
    </Text>
  )
}
