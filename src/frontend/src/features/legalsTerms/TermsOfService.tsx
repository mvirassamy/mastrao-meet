import { Fragment } from 'react'
import { Screen } from '@/layout/Screen'
import { H, P } from '@/primitives'
import { HStack } from '@/styled-system/jsx'
import { useTranslation } from 'react-i18next'

type UsageSection = {
  title: string
  paragraphs: string[]
}

const TermsOfServiceRoute = () => {
  const { t } = useTranslation('termsOfService')
  const sections = t('sections', { returnObjects: true }) as UsageSection[]

  return (
    <Screen layout="centered" headerTitle={t('title')}>
      <HStack display="block" padding="2rem">
        <P>{t('introduction')}</P>
        {sections.map((section) => (
          <Fragment key={section.title}>
            <H lvl={2}>{section.title}</H>
            {section.paragraphs.map((paragraph) => (
              <P key={paragraph}>{paragraph}</P>
            ))}
          </Fragment>
        ))}
      </HStack>
    </Screen>
  )
}

export default TermsOfServiceRoute
