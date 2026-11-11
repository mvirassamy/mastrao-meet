import { LinkButton } from '@/primitives'
import { useTranslation } from 'react-i18next'
import { useConfig } from '@/api/useConfig'
import { ProConnectButton } from './ProConnectButton'
import { authUrl } from '@/features/auth/utils/authUrl'

type LoginButtonProps = {
  size?: 'default' | 'sm'
  proConnectHint?: boolean // Hide hint in layouts where space doesn't allow it.
  /** Page shown after login; the current page by default. */
  returnTo?: string
}

export const LoginButton = ({
  proConnectHint = true,
  size = 'default',
  returnTo,
}: LoginButtonProps) => {
  const { t } = useTranslation('global', { keyPrefix: 'login' })
  const { data } = useConfig()

  if (data?.use_proconnect_button) {
    return <ProConnectButton hint={proConnectHint} returnTo={returnTo} />
  }

  return (
    <LinkButton
      size={size}
      href={authUrl({ returnTo })}
      data-attr="login"
      variant="default"
    >
      {t('buttonLabel')}
    </LinkButton>
  )
}
