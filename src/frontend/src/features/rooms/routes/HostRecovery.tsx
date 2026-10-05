import { useRef, useState } from 'react'
import { useParams } from 'wouter'
import { useTranslation } from 'react-i18next'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import { useUser } from '@/features/auth/api/useUser'
import { authUrl } from '@/features/auth/utils/authUrl'
import { Screen } from '@/layout/Screen'
import { Button, H, Text } from '@/primitives'
import { VStack } from '@/styled-system/jsx'

const HostRecovery = () => {
  const { roomRef } = useParams<{ roomRef: string }>()
  const { isLoggedIn } = useUser()
  const { t } = useTranslation('rooms', { keyPrefix: 'hostRecovery' })
  const storageKey = `mastrao-host-recovery:${roomRef}`
  const key = useRef(sessionStorage.getItem(storageKey) || crypto.randomUUID())
  const [pending, setPending] = useState(false)
  const [failed, setFailed] = useState(false)

  const recover = async () => {
    if (pending || isLoggedIn === undefined) return
    if (!isLoggedIn) {
      window.location.assign(authUrl())
      return
    }
    setPending(true)
    setFailed(false)
    sessionStorage.setItem(storageKey, key.current)
    try {
      const result = await fetchApi<{ room_url: string }>(
        `rooms/${encodeURIComponent(roomRef)}/host-handoff/`,
        {
          method: 'POST',
          headers: { 'X-Idempotency-Key': key.current },
        }
      )
      if (result.room_url !== `/${roomRef}`)
        throw new Error('Host recovery binding mismatch')
      window.location.assign(result.room_url)
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 401) {
        window.location.assign(authUrl())
        return
      }
      if (error instanceof ApiError && error.statusCode === 404) {
        key.current = crypto.randomUUID()
        sessionStorage.removeItem(storageKey)
      }
      setFailed(true)
    } finally {
      setPending(false)
    }
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
