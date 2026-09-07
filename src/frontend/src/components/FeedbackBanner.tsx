import { css } from '@/styled-system/css'
import { RiErrorWarningFill, RiExternalLinkFill } from '@remixicon/react'
import { useTranslation } from 'react-i18next'
import { Text, A } from '@/primitives'
import { useConfig } from '@/api/useConfig'

export const FeedbackBanner = () => {
  const { t } = useTranslation()
  const { data } = useConfig()

  if (!data?.feedback?.url) return

  return (
    <div
      className={css({
        width: '100%',
        backgroundColor: 'info',
        color: 'ring',
        display: { base: 'none', sm: 'flex' },
        justifyContent: 'center',
        padding: '0.5rem 0',
      })}
    >
      <div
        className={css({
          display: 'inline-flex',
          gap: '0.5rem',
          alignItems: 'center',
        })}
      >
        <RiErrorWarningFill size={20} aria-hidden="true" />
        <Text as="p" variant="sm">
          {t('feedback.context')}
        </Text>
        <div
          className={css({
            display: 'flex',
            alignItems: 'center',
            gap: 0.25,
          })}
        >
          <A href={data?.feedback?.url} target="_blank" size="sm">
            {t('feedback.cta')}
          </A>
          <RiExternalLinkFill size={16} aria-hidden="true" />
        </div>
      </div>
    </div>
  )
}
