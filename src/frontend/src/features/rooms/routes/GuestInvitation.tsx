import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import { VStack } from '@/styled-system/jsx'
import { ApiError } from '@/api/ApiError'
import { Screen } from '@/layout/Screen'
import { Button, H, Text } from '@/primitives'
import {
  redeemGuestInvitation,
  redeemGuestShare,
} from '../api/redeemGuestInvitation'
import { clearPlatformReturnForRoomUrl } from '../platformReturn'
import { MissingGuestLink } from '../components/MissingGuestLink'
import {
  type GuestLink,
  consumeGuestInvitationFragment,
  guestRedemptionId,
  forgetGuestRedemption,
} from '../utils/guestInvitationFragment'

const GuestInvitation = () => {
  const { t } = useTranslation()
  const [invitation, setInvitation] = useState(consumeGuestInvitationFragment)
  const redemptionId = useRef(invitation ? guestRedemptionId(invitation) : '')
  const [status, setStatus] = useState<
    'idle' | 'loading' | 'terminal-error' | 'temporary-error'
  >('idle')

  const redeem = async (link: GuestLink) => {
    if (status === 'loading') return
    setStatus('loading')
    try {
      const result =
        link.kind === 'durable'
          ? await redeemGuestShare(
              link.organization,
              link.share,
              redemptionId.current,
              link.choiceToken
            )
          : await redeemGuestInvitation(link.invitation, redemptionId.current)
      clearPlatformReturnForRoomUrl(result.room_url)
      window.location.assign(`${result.room_url}?silentLogin=false`)
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 404)
        forgetGuestRedemption(link)
      setStatus(
        error instanceof ApiError && error.statusCode === 404
          ? 'terminal-error'
          : 'temporary-error'
      )
    }
  }

  const joinWithPastedLink = (link: GuestLink) => {
    redemptionId.current = guestRedemptionId(link)
    setInvitation(link)
    void redeem(link)
  }

  return (
    <Screen layout="centered" header={false} footer={false}>
      <VStack
        gap="1.5rem"
        alignItems="center"
        textAlign="center"
        className={css({ maxWidth: '32rem', paddingX: '1.5rem' })}
      >
        <img
          src="/assets/illustrations/invitation.webp"
          alt=""
          width={768}
          height={328}
          decoding="async"
          className={css({
            display: 'block',
            width: { base: '240px', md: '320px' },
            height: 'auto',
            userSelect: 'none',
            pointerEvents: 'none',
          })}
        />
        {!invitation && <MissingGuestLink onLink={joinWithPastedLink} />}
        {invitation && (
          <>
            <H lvl={1} margin={false} centered>
              {t('guestInvitation.title')}
            </H>
            <Text as="p" variant="note">
              {t('guestInvitation.body')}
            </Text>
          </>
        )}
        {invitation && status !== 'terminal-error' && (
          <Button
            onPress={() => void redeem(invitation)}
            loading={status === 'loading'}
            isDisabled={status === 'loading'}
          >
            {t(
              status === 'temporary-error'
                ? 'guestInvitation.retry'
                : 'guestInvitation.submit'
            )}
          </Button>
        )}
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
