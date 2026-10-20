import { Screen } from '@/layout/Screen'
import { H, P, A, Link } from '@/primitives'
import { HStack } from '@/styled-system/jsx'
import { useTranslation } from 'react-i18next'

const LegalTermsRoute = () => {
  const { t } = useTranslation('legals')

  return (
    <Screen layout="centered" headerTitle={t('title')}>
      <HStack display="block" padding="2rem">
        <H lvl={2}>{t('service.title')}</H>
        <P>{t('service.body')}</P>
        <H lvl={2}>{t('publication.title')}</H>
        <P>{t('publication.body')}</P>
        <H lvl={2}>{t('accessibility.title')}</H>
        <P>
          <Link to="/accessibilite" color="primary">
            {t('accessibility.link')}
          </Link>
        </P>
        <H lvl={2}>{t('upstream.title')}</H>
        <P>{t('upstream.body')}</P>
        <P>
          <A href="https://github.com/suitenumerique/meet" color="primary">
            {t('upstream.link')}
          </A>
        </P>
        <P>{t('upstream.license')}</P>
      </HStack>
    </Screen>
  )
}

export default LegalTermsRoute
