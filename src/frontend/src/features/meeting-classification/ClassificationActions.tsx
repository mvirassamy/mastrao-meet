import { useTranslation } from 'react-i18next'
import { Button, LinkButton } from '@/primitives'
import { css } from '@/styled-system/css'

/** The result card and summary expose the same authorized destination. */
export function ClassificationActions({
  canClassify,
  matterUrl,
  onClassify,
}: {
  canClassify: boolean
  matterUrl: string | null
  onClassify: () => void
}) {
  const { t } = useTranslation('meetingHistory')
  if (!canClassify && !matterUrl) return null
  return (
    <div className={css({ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' })}>
      {canClassify && (
        <Button size="sm" variant="outline" onPress={onClassify}>
          {t('classification.title')}
        </Button>
      )}
      {matterUrl && (
        <LinkButton
          size="sm"
          variant="outline"
          href={matterUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          {t('classification.openMatter')}
        </LinkButton>
      )}
    </div>
  )
}
