import { useTranslation } from 'react-i18next'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import type { MeetingClassification } from './classificationApi'
import type { MatterDestination } from './ClassificationDialog'

export function ClassificationStatus({
  classification,
  matter,
  loading,
  failed,
  onRetry,
  onOpen,
}: {
  classification?: MeetingClassification
  matter: MatterDestination | null
  loading: boolean
  failed: boolean
  onRetry: () => void
  onOpen: () => void
}) {
  const { t } = useTranslation('meetingHistory')
  if (loading) return <p role="status">{t('classification.loadingCurrent')}</p>
  if (failed || !classification)
    return (
      <div role="alert">
        <p>{t('classification.unavailable')}</p>
        <Button size="sm" variant="secondary" onPress={onRetry}>
          {t('error.retry')}
        </Button>
      </div>
    )
  let label = t('classification.unclassified')
  if (classification.matter_ref) label = t('classification.filed')
  if (matter)
    label = t('classification.current', { name: matter.display_label })
  return (
    <div
      className={css({
        display: 'flex',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: '0.75rem',
        marginBottom: '1rem',
      })}
    >
      <p role="status">{label}</p>
      {classification.can_classify && (
        <Button size="sm" variant="outline" onPress={onOpen}>
          {t('classification.title')}
        </Button>
      )}
    </div>
  )
}
