import { useParams } from 'wouter'
import { useTranslation } from 'react-i18next'
import { useUser } from '@/features/auth/api/useUser'
import { authUrl } from '@/features/auth/utils/authUrl'
import { Screen } from '@/layout/Screen'
import { Button, H, Text } from '@/primitives'
import { VStack } from '@/styled-system/jsx'
import { useHostHandoff } from '../hooks/useHostHandoff'

const HostRecovery = () => {
  const { roomRef } = useParams<{ roomRef: string }>()
  const { isLoggedIn } = useUser()
  const { t } = useTranslation('rooms', { keyPrefix: 'hostRecovery' })
  const { handoff, pending, failed } = useHostHandoff(roomRef)

  const recover = () => {
    if (pending || isLoggedIn === undefined) return
    if (!isLoggedIn) {
      window.location.assign(authUrl())
      return
    }
    void handoff()
  }

  return (
    <Screen layout="centered" header={false} footer={false}>
      <VStack gap="1.5rem" padding="1.5rem" textAlign="center">
        <H lvl={1} centered>
          {t('title')}
        </H>
        <Text>{t('body')}</Text>
        <Button
          onPress={recover}
          isDisabled={pending || isLoggedIn === undefined}
          loading={pending}
        >
          {t('submit')}
        </Button>
        {failed && <Text role="alert">{t('error')}</Text>}
      </VStack>
    </Screen>
  )
}

export default HostRecovery
