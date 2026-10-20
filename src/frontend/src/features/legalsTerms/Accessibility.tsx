import { Screen } from '@/layout/Screen'
import { H, P, A } from '@/primitives'
import { HStack } from '@/styled-system/jsx'
import { useTranslation } from 'react-i18next'
import { useConfig } from '@/api/useConfig'

const AccessibilityRoute = () => {
  const { t } = useTranslation('accessibility', { keyPrefix: 'accessibility' })
  const { data } = useConfig()

  return (
    <Screen layout="centered" headerTitle={t('title')}>
      <HStack display="block" padding="2rem">
        <P>{t('introduction')}</P>
        <P>{t('scope')}</P>
        <H lvl={2} bold>
          {t('complianceStatus.title')}
        </H>
        <P>{t('complianceStatus.body')}</P>
        <H lvl={2} bold>
          {t('improvement.title')}
        </H>
        <P>{t('improvement.body')}</P>
        {data?.documentation_url && (
          <P>
            <A href={data.documentation_url} color="primary">
              {t('improvement.documentation')}
            </A>
          </P>
        )}
      </HStack>
    </Screen>
  )
}

export default AccessibilityRoute
