import { LinkButton } from '@/primitives'
import { useTranslation } from 'react-i18next'
import { useConfig } from '@/api/useConfig'
import { ProConnectButton } from './ProConnectButton'
import { authUrl } from '@/features/auth/utils/authUrl'

type LoginButtonProps = {
  size?: 'default' | 'sm'
  proConnectHint?: boolean // Hide hint in layouts where space doesn't allow it.
}

export const LoginButton = ({
  proConnectHint = true,
  size = 'default',
}: LoginButtonProps) => {
  const { t } = useTranslation('global', { keyPrefix: 'login' })
  const { data } = useConfig()

  if (data?.use_proconnect_button) {
    return <ProConnectButton hint={proConnectHint} />
  }

  return (
    <LinkButton
      size={size}
      href={authUrl()}
      data-attr="login"
      variant="primary"
    >
      {t('buttonLabel')}
    </LinkButton>
  )
}
