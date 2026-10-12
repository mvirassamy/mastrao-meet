import { useTranslation } from 'react-i18next'
import { H, LinkButton, Text } from '@/primitives'

/**
 * Shown when the server no longer accepts the guest link (expired, replaced
 * or disabled): only the organizer can send a new one.
 */
export const UnavailableGuestLink = () => {
  const { t } = useTranslation()
  return (
    <>
      <H lvl={1} margin={false} centered>
        {t('guestInvitation.unavailable.title')}
      </H>
      <Text as="p" variant="note" role="alert">
        {t('guestInvitation.unavailable.body')}
      </Text>
      <LinkButton href="/" variant="outline">
        {t('backToHome')}
      </LinkButton>
    </>
  )
}
