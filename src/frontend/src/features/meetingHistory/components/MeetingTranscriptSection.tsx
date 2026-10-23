import { useTranslation } from 'react-i18next'
import type { MeetingTranscript } from '../api/types'
import { SECTION_ICONS } from './meetingContent'
import { MeetingSectionState } from './MeetingHistoryStatePanel'
import { MeetingContentSection, TranscriptBody } from './MeetingResultSections'

export function MeetingTranscriptSection({
  transcript,
}: {
  transcript: MeetingTranscript
}) {
  const { t } = useTranslation('meetingHistory')
  let statusLabel: string | undefined
  let body = <TranscriptBody transcript={transcript} />

  if (
    transcript.status === 'transcribing' &&
    transcript.projection.processing_state === 'temporarily_suspended'
  ) {
    statusLabel = t('transcript.temporarilySuspended.title', {
      defaultValue: 'Transcription temporairement suspendue côté Mastrao',
    })
    body = (
      <MeetingSectionState
        icon={SECTION_ICONS.transcript}
        look="empty"
        title={statusLabel}
        description={t('transcript.temporarilySuspended.description', {
          defaultValue:
            'Augmenter votre seuil d’alerte utilisateur ne réactive pas la transcription.',
        })}
      />
    )
  }

  return (
    <MeetingContentSection
      kind="transcript"
      statusKey={`status.transcript.${transcript.status}`}
      statusLabel={statusLabel}
    >
      {body}
    </MeetingContentSection>
  )
}
