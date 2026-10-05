import { useHostRecovery } from '../hooks/useHostRecovery'
import { useParams } from 'wouter'
import { useTranslation } from 'react-i18next'
import { Screen } from '@/layout/Screen'
import { Button, H, Text } from '@/primitives'
import { VStack } from '@/styled-system/jsx'

const HostRecovery = () => {
  const { roomRef } = useParams<{ roomRef: string }>()
  const { recover, pending, failed, isDisabled } = useHostRecovery(roomRef)
  const { t } = useTranslation('rooms', { keyPrefix: 'hostRecovery' })

  return (
    <Screen layout="centered" header={false} footer={false}>
      <VStack gap="1.5rem" padding="1.5rem" textAlign="center">
        <H lvl={1} centered>
          {t('title')}
        </H>
        <Text>{t('body')}</Text>
        <Button onPress={recover} isDisabled={isDisabled} loading={pending}>
          {t('submit')}
        </Button>
        {failed && <Text role="alert">{t('error')}</Text>}
      </VStack>
    </Screen>
  )
}

export default HostRecovery
