import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import { VStack } from '@/styled-system/jsx'
import { ApiError } from '@/api/ApiError'
import { Screen } from '@/layout/Screen'
import { Button, H, Text } from '@/primitives'
import { redeemHostInvitation } from '../api/redeemHostInvitation'
import { clearPlatformReturnForRoomUrl } from '../platformReturn'
import { consumeHostInvitationFragment } from '../utils/hostInvitationFragment'

type ExchangeStatus = 'loading' | 'terminal-error' | 'temporary-error'

const HostInvitation = () => {
  const { t } = useTranslation()
  const invitation = consumeHostInvitationFragment()
  const exchangeId = useRef(
    `exchange_${crypto.randomUUID().replaceAll('-', '')}`
  )
  const exchangeStarted = useRef(false)
  const [status, setStatus] = useState<ExchangeStatus>('loading')

  const exchange = useCallback(async () => {
    if (!invitation) {
      setStatus('terminal-error')
      return
    }
    setStatus('loading')
    try {
      const result = await redeemHostInvitation(invitation, exchangeId.current)
      clearPlatformReturnForRoomUrl(result.room_url)
      window.location.assign(`${result.room_url}?silentLogin=false`)
    } catch (error) {
      setStatus(
        error instanceof ApiError && error.statusCode === 404
          ? 'terminal-error'
          : 'temporary-error'
      )
    }
  }, [invitation])

  useEffect(() => {
    if (exchangeStarted.current) return
    exchangeStarted.current = true
    void exchange()
  }, [exchange])

  const renderExchangeStatus = () => {
    switch (status) {
      case 'loading':
        return <Text as="p">{t('hostInvitation.loading')}</Text>
      case 'terminal-error':
        return (
          <Text as="p" role="alert">
            {t('hostInvitation.error')}
          </Text>
        )
      case 'temporary-error':
        return (
          <>
            <Text as="p" role="alert">
              {t('hostInvitation.temporaryError')}
            </Text>
            <Button onPress={exchange}>{t('hostInvitation.retry')}</Button>
          </>
        )
    }
  }

  return (
    <Screen layout="centered" header={false} footer={false}>
      <VStack
        gap="1.5rem"
        alignItems="center"
        textAlign="center"
        className={css({ maxWidth: '32rem', paddingX: '1.5rem' })}
      >
        <H lvl={1} margin={false} centered>
          {t('hostInvitation.title')}
        </H>
        {renderExchangeStatus()}
      </VStack>
    </Screen>
  )
}

export default HostInvitation
