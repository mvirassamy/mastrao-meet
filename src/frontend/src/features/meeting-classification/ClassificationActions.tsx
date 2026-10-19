import { useTranslation } from 'react-i18next'
import { Button, LinkButton } from '@/primitives'
import { css } from '@/styled-system/css'

/** Links aligned with the text before them. */
const inlineAction = css({ paddingX: '0!', minHeight: 'auto!' })

/** Inline links next to the meeting's dossier line. */
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
    <div className={css({ display: 'flex', flexWrap: 'wrap', gap: '0.75rem' })}>
      {canClassify && (
        <Button
          size="xs"
          variant="link"
          className={inlineAction}
          onPress={onClassify}
        >
          {t('classification.title')}
        </Button>
      )}
      {matterUrl && (
        <LinkButton
          size="xs"
          variant="link"
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
