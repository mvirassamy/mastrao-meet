import { useTranslation } from 'react-i18next'
import { Button, Dialog } from '@/primitives'
import { css } from '@/styled-system/css'
import { formatTranscriptTimestamp } from '@/features/meetingHistory/utils/meetingHistoryFormat'
import type { MeetingAssistantSegment } from './types'

export type CitationState =
  | { phase: 'closed' }
  | { phase: 'loading' }
  | { phase: 'error' }
  | { phase: 'ready'; segment: MeetingAssistantSegment }

export function AssistantCitationDialog({
  state,
  onClose,
}: {
  state: CitationState
  onClose: () => void
}) {
  const { t } = useTranslation('meetingAssistant')
  function renderPassage() {
    switch (state.phase) {
      case 'closed':
        return null
      case 'loading':
        return <p role="status">{t('source.loading')}</p>
      case 'error':
        return <p role="alert">{t('source.error')}</p>
      case 'ready':
        return (
          <>
            <p>
              {t(`source.status.${state.segment.status}`)}
              {' · '}
              {t('source.version', {
                version: state.segment.citation.source_version,
              })}
            </p>
            <p>
              {state.segment.speaker && (
                <>
                  {state.segment.speaker}
                  {' · '}
                </>
              )}
              {formatTranscriptTimestamp(state.segment.citation.start_ms)}
              {' · '}
              {t(`source.time.${state.segment.citation.time_basis}`)}
            </p>
            <blockquote
              className={css({
                margin: 0,
                borderLeft: '2px solid token(colors.border)',
                paddingLeft: '1rem',
                whiteSpace: 'pre-wrap',
                overflowWrap: 'anywhere',
                lineHeight: 1.6,
              })}
            >
              {state.segment.text}
            </blockquote>
          </>
        )
    }
  }
  return (
    <Dialog
      isOpen={state.phase !== 'closed'}
      title={t('source.title')}
      appearance="app"
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      {renderPassage()}
      <Button variant="secondary" onPress={onClose}>
        {t('source.close')}
      </Button>
    </Dialog>
  )
}
