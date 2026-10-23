import { cleanup, render, screen } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, describe, expect, it, vi } from 'vitest'
import fr from '@/locales/fr/meetingHistory.json'
import {
  mergeHistoryDetail,
  normalizeHistoryDetail,
} from '../api/meetingHistoryApi'
import type { MeetingContentStatus } from '../api/types'
import { SummaryBody } from './MeetingResultSections'
import { MeetingTranscriptSection } from './MeetingTranscriptSection'

const i18n = createInstance()
await i18n.init({
  lng: 'fr',
  resources: { fr: { meetingHistory: fr } },
  initAsync: false,
})

afterEach(cleanup)

const suspendedTitle = 'Transcription temporairement suspendue côté Mastrao'
const thresholdExplanation =
  'Augmenter votre seuil d’alerte utilisateur ne réactive pas la transcription.'
const digest = 'a'.repeat(64)
const projection = (state: MeetingContentStatus = 'transcribing') => ({
  version: 1,
  state,
  revision: 1,
  digest,
  source: null,
})

const detail = (fields: Record<string, unknown> = {}) => {
  const meeting = normalizeHistoryDetail({
    id: 'meeting-1',
    started_at: '2026-10-16T08:00:00Z',
    transcript_status: 'processing',
    ...fields,
  })
  if (!meeting) throw new Error('Invalid test meeting')
  return meeting
}

const renderTranscript = (meeting: ReturnType<typeof detail>) =>
  render(
    <I18nextProvider i18n={i18n}>
      <MeetingTranscriptSection transcript={meeting.transcript} />
    </I18nextProvider>
  )

describe('temporarily suspended history transcription', () => {
  it('preserves the suspension marker received from Platform', () => {
    const meeting = detail({
      transcript_projection: {
        ...projection(),
        processing_state: 'temporarily_suspended',
      },
    })

    expect(meeting.transcript.projection.processing_state).toBe(
      'temporarily_suspended'
    )
    expect(meeting.transcript.status).toBe('transcribing')
  })

  it('shows and announces suspension without promising a threshold unlock', () => {
    const meeting = detail()
    meeting.transcript.projection.processing_state = 'temporarily_suspended'
    renderTranscript(meeting)

    expect(meeting.transcript.status).toBe('transcribing')
    expect(screen.getByRole('status').textContent).toBe(suspendedTitle)
    expect(screen.getAllByText(suspendedTitle)).toHaveLength(2)
    expect(screen.getByText(thresholdExplanation)).toBeTruthy()
    expect(screen.queryByText(fr.transcript.transcribing.title)).toBeNull()
    expect(
      screen.queryByText(fr.transcript.transcribing.description)
    ).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it.each([{}, { transcript_projection: projection() }])(
    'keeps existing processing wording for an older payload: %j',
    (fields) => {
      const meeting = detail(fields)
      renderTranscript(meeting)

      expect(meeting.transcript.projection.processing_state).toBeUndefined()
      expect(screen.getByRole('status').textContent).toBe(
        fr.status.transcript.transcribing
      )
      expect(screen.getByText(fr.transcript.transcribing.title)).toBeTruthy()
      expect(
        screen.getByText(fr.transcript.transcribing.description)
      ).toBeTruthy()
      expect(screen.queryByText(suspendedTitle)).toBeNull()
      expect(screen.queryByText(thresholdExplanation)).toBeNull()
    }
  )

  it.each(['budget_exhausted', true, { ceiling_micros: 1000 }])(
    'ignores a processing marker outside the closed contract: %j',
    (processingState) => {
      const meeting = detail({
        transcript_projection: {
          ...projection(),
          processing_state: processingState,
        },
      })
      renderTranscript(meeting)

      expect(meeting.transcript.projection.processing_state).toBeUndefined()
      expect(screen.queryByText(suspendedTitle)).toBeNull()
      expect(screen.getByText(fr.transcript.transcribing.title)).toBeTruthy()
    }
  )

  it('clears suspension when a subsequent status resumes processing at the same revision', () => {
    const current = detail({
      transcript_projection: {
        ...projection(),
        processing_state: 'temporarily_suspended',
      },
    })
    const incoming = detail({ transcript_projection: projection() })
    const merged = mergeHistoryDetail(current, incoming)
    renderTranscript(merged)

    expect(merged.transcript.projection.processing_state).toBeUndefined()
    expect(screen.queryByText(suspendedTitle)).toBeNull()
    expect(screen.getByText(fr.transcript.transcribing.title)).toBeTruthy()
  })

  it.each(['available', 'partial'] as const)(
    'keeps a %s transcript and summary readable even if a suspension marker is present',
    (state) => {
      const meeting = detail({
        transcript_projection: {
          ...projection(state),
          processing_state: 'temporarily_suspended',
        },
        summary_projection: {
          ...projection('available'),
          source: { revision: 1, digest },
        },
        transcript: {
          segments: [
            { id: 's1', start_ms: 0, text: 'Transcription conservée.' },
          ],
        },
        summary: { text: 'Synthèse conservée.' },
      })
      meeting.transcript.projection.processing_state = 'temporarily_suspended'
      render(
        <I18nextProvider i18n={i18n}>
          <SummaryBody
            summary={meeting.summary}
            view={meeting.summary.status}
            onRetry={vi.fn()}
          />
          <MeetingTranscriptSection transcript={meeting.transcript} />
        </I18nextProvider>
      )

      expect(screen.getByText('Transcription conservée.')).toBeTruthy()
      expect(screen.getByText('Synthèse conservée.')).toBeTruthy()
      expect(screen.queryByText(suspendedTitle)).toBeNull()
      expect(screen.queryByText(thresholdExplanation)).toBeNull()
    }
  )

  it('keeps a readable cached result when a suspension arrives for the same revision', () => {
    const current = detail({
      transcript_projection: projection('available'),
      transcript: {
        segments: [{ id: 's1', start_ms: 0, text: 'Transcription conservée.' }],
      },
    })
    const incoming = detail({
      transcript_projection: {
        ...projection(),
        processing_state: 'temporarily_suspended',
      },
    })
    renderTranscript(mergeHistoryDetail(current, incoming))

    expect(screen.getByText('Transcription conservée.')).toBeTruthy()
    expect(screen.queryByText(suspendedTitle)).toBeNull()
  })

  it.each(['failed', 'waiting_for_audio'] as const)(
    'does not replace the existing %s state with suspension wording',
    (state) => {
      const meeting = detail({ transcript_projection: projection(state) })
      meeting.transcript.projection.processing_state = 'temporarily_suspended'
      renderTranscript(meeting)

      expect(screen.getByText(fr.transcript[state].title)).toBeTruthy()
      expect(screen.queryByText(suspendedTitle)).toBeNull()
    }
  )
})
