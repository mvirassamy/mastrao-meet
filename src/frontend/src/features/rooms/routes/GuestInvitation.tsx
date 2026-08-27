import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import { VStack } from '@/styled-system/jsx'
import { ApiError } from '@/api/ApiError'
import { Screen } from '@/layout/Screen'
import { Button, H, Text } from '@/primitives'
import { redeemGuestInvitation } from '../api/redeemGuestInvitation'
import { clearPlatformReturnForRoomUrl } from '../platformReturn'
import { consumeGuestInvitationFragment } from '../utils/guestInvitationFragment'

const GuestInvitation = () => {
  const { t } = useTranslation()
  const invitation = consumeGuestInvitationFragment()
  const redemptionId = useRef(
    `redemption_${crypto.randomUUID().replaceAll('-', '')}`
  )
  const [status, setStatus] = useState<
    'idle' | 'loading' | 'terminal-error' | 'temporary-error'
  >('idle')

  const redeem = async () => {
    if (!invitation || status === 'loading') return
    setStatus('loading')
    try {
      const result = await redeemGuestInvitation(
        invitation,
        redemptionId.current
      )
      clearPlatformReturnForRoomUrl(result.room_url)
      window.location.assign(`${result.room_url}?silentLogin=false`)
    } catch (error) {
      setStatus(
        error instanceof ApiError && error.statusCode === 404
          ? 'terminal-error'
          : 'temporary-error'
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
          {t('guestInvitation.title')}
        </H>
        <Text as="p" variant="note">
          {t('guestInvitation.body')}
        </Text>
        {!invitation ? (
          <Text as="p">{t('guestInvitation.missing')}</Text>
        ) : status !== 'terminal-error' ? (
          <Button
            onPress={redeem}
            loading={status === 'loading'}
            isDisabled={status === 'loading'}
          >
            {t(
              status === 'temporary-error'
                ? 'guestInvitation.retry'
                : 'guestInvitation.submit'
            )}
          </Button>
        ) : null}
        {status === 'terminal-error' && (
          <Text as="p" role="alert">
            {t('guestInvitation.error')}
          </Text>
        )}
        {status === 'temporary-error' && (
          <Text as="p" role="alert">
            {t('guestInvitation.temporaryError')}
          </Text>
        )}
      </VStack>
    </Screen>
  )
}

export default GuestInvitation
