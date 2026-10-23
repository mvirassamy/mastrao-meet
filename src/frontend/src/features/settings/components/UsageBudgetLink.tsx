import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useUser } from '@/features/auth/api/useUser'
import { LinkButton, P } from '@/primitives'
import { fetchUsageBudget } from '../api/fetchUsageBudget'

export const UsageBudgetLink = () => {
  const { t } = useTranslation('settings')
  const { user, isLoggedIn } = useUser()
  const { data, isError } = useQuery({
    queryKey: ['usage-budget', user?.id],
    queryFn: fetchUsageBudget,
    enabled: isLoggedIn === true && !!user,
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })

  if (!isLoggedIn || !user) return null
  if (isError) return <P role="status">{t('usageBudget.unavailable')}</P>
  if (!data) return null

  return (
    <LinkButton
      variant="link"
      href={data.usage_budget_url}
      target="_blank"
      rel="noopener noreferrer"
      tooltip={t('usageBudget.opensInNewTab')}
      aria-label={`${t('usageBudget.link')} (${t('usageBudget.opensInNewTab')})`}
    >
      {t('usageBudget.link')}
    </LinkButton>
  )
}
