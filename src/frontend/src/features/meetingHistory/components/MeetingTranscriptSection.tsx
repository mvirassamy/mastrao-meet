import { useTranslation } from 'react-i18next'
import type { MeetingTranscript } from '../api/types'
import { MeetingContentSection, TranscriptBody } from './MeetingResultSections'

export function MeetingTranscriptSection({
  transcript,
  meetingRef,
}: {
  transcript: MeetingTranscript
  meetingRef?: string
}) {
  const { t } = useTranslation('meetingHistory')
  let statusLabel: string | undefined

  if (
    transcript.status === 'transcribing' &&
    transcript.projection.processing_state === 'temporarily_suspended'
  ) {
    statusLabel = t('transcript.temporarilySuspended.title', {
      defaultValue: 'Transcription temporairement suspendue côté Mastrao',
    })
  }

  return (
    <MeetingContentSection
      kind="transcript"
      statusKey={`status.transcript.${transcript.status}`}
      statusLabel={statusLabel}
    >
      <TranscriptBody transcript={transcript} meetingRef={meetingRef} />
    </MeetingContentSection>
  )
}
