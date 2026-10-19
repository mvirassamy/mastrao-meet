import { useTranslation } from 'react-i18next'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import { ClassificationActions } from './ClassificationActions'
import type { MeetingClassification } from './classificationApi'
import type { MatterDestination } from './ClassificationDialog'

export const DOSSIER_ICON = '/assets/illustrations/dossier.webp'

const line = css({
  display: 'flex',
  alignItems: 'center',
  flexWrap: 'wrap',
  columnGap: '0.75rem',
  rowGap: '0.25rem',
  marginTop: '0.625rem',
  fontSize: '0.8125rem',
  color: 'muted-foreground',
})

/** The meeting's dossier as one quiet line under the date. */
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
        <Button
          size="xs"
          variant="link"
          className={css({ paddingX: '0!', minHeight: 'auto!' })}
          onPress={onRetry}
        >
          {t('error.retry')}
        </Button>
      </div>
    )
  let label = t('classification.unclassified')
  if (classification.matter_ref) label = t('classification.filed')
  if (matter)
    label = t('classification.current', { name: matter.display_label })
  return (
    <div className={line}>
      <span
        className={css({
          display: 'inline-flex',
          alignItems: 'center',
          gap: '0.375rem',
          color: matter ? 'var(--heading-foreground)' : 'muted-foreground',
          fontWeight: matter ? 500 : 400,
        })}
      >
        <img
          src={DOSSIER_ICON}
          alt=""
          aria-hidden="true"
          width={18}
          height={18}
        />
        <span role="status">{label}</span>
      </span>
      <ClassificationActions
        canClassify={classification.can_classify}
        matterUrl={matterUrl}
        onClassify={onOpen}
      />
    </div>
  )
}
