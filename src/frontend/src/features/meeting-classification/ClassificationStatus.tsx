import { useTranslation } from 'react-i18next'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import { FiledPill, UnfiledPill } from './ClassificationPill'
import type { MeetingClassification } from './classificationApi'
import type { MatterDestination } from './ClassificationDialog'

export { DOSSIER_ICON } from './ClassificationPill'

const line = css({
  display: 'flex',
  alignItems: 'center',
  flexWrap: 'wrap',
  columnGap: '0.75rem',
  rowGap: '0.25rem',
  marginTop: '0.75rem',
  fontSize: '0.8125rem',
  color: 'muted-foreground',
})

/** The meeting's dossier as a pill under the date. */
export function ClassificationStatus({
  classification,
  matter,
  matterUrl,
  loading,
  failed,
  onRetry,
  onOpen,
}: {
  classification?: MeetingClassification
  matter: MatterDestination | null
  matterUrl: string | null
  loading: boolean
  failed: boolean
  onRetry: () => void
  onOpen: () => void
}) {
  const { t } = useTranslation('meetingHistory')
  if (loading)
    return (
      <p role="status" className={line}>
        {t('classification.loadingCurrent')}
      </p>
    )
  if (failed || !classification)
    return (
      <div role="alert" className={line}>
        <p>{t('classification.unavailable')}</p>
        <Button size="sm" variant="outline" onPress={onRetry}>
          {t('error.retry')}
        </Button>
      </div>
    )
  if (!classification.matter_ref)
    return (
      <div className={line}>
        <UnfiledPill
          canClassify={classification.can_classify}
          onClassify={onOpen}
        />
      </div>
    )
  const name = matter?.display_label ?? t('classification.filed')
  return (
    <div role="status" className={line}>
      <FiledPill
        name={name}
        accessibleName={t('classification.current', { name })}
        matterUrl={matterUrl}
        canClassify={classification.can_classify}
        onClassify={onOpen}
      />
    </div>
  )
}
