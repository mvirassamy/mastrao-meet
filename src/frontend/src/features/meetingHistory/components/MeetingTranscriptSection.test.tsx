import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { createInstance } from 'i18next'
import type { ReactNode } from 'react'
import { I18nextProvider } from 'react-i18next'
import { afterEach, describe, expect, it, vi } from 'vitest'
import fr from '@/locales/fr/meetingHistory.json'
import en from '@/locales/en/meetingHistory.json'
import {
  mergeHistoryDetail,
  normalizeHistoryDetail,
} from '../api/meetingHistoryApi'
import type { MeetingContentStatus } from '../api/types'
import { SummaryBody } from './MeetingResultSections'
import { MeetingTranscriptSection } from './MeetingTranscriptSection'

// Exercise the transcript renderer without shared icon/control dependencies.
vi.mock('@/icons', () => ({
  GroupIcon: () => null,
  InformationIcon: () => null,
  RetryIcon: () => null,
  CheckIcon: () => null,
  CopyIcon: () => null,
}))
vi.mock('@/primitives', () => ({
  Button: ({
    children,
    onPress,
  }: {
    children: ReactNode
    onPress?: () => void
  }) => (
    <button type="button" onClick={onPress}>
      {children}
    </button>
  ),
}))

const i18n = createInstance()
await i18n.init({
  lng: 'fr',
  resources: { fr: { meetingHistory: fr } },
  initAsync: false,
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

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

describe('history transcript epoch fidelity', () => {
  it('renders separate turns and local times for the same speaker after reconnection', () => {
    const epochA = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
    const epochB = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    const meeting = detail({
      transcript_projection: projection('partial'),
      transcript: {
        segments: [
          {
            id: 's1',
            start_ms: 5000,
            text: '  De\u0301cision : ne pas payer.\n',
            epoch: epochA,
          },
          {
            id: 's2',
            start_ms: 6000,
            text: 'Montant : 250 €.  ',
            epoch: epochA,
          },
          { id: 's1', start_ms: 0, text: '  Reconnexion.\n', epoch: epochB },
          {
            id: 's2',
            start_ms: 5500,
            text: 'Ne pas confirmer.  ',
            epoch: epochB,
          },
        ].map(({ epoch, ...segment }) => ({
          ...segment,
          speaker: 'Camille',
          source_epoch: { epoch_ref: epoch, time_basis: 'source_relative_ms' },
        })),
      },
    })
    renderTranscript(meeting)

    const turns = screen.getAllByRole('listitem')
    expect(turns).toHaveLength(2)
    expect(turns.map((turn) => turn.querySelector('p')?.textContent)).toEqual([
      'CamilleTemps local 00:05',
      'CamilleTemps local 00:00',
    ])
    expect(
      turns.map((turn) =>
        Array.from(turn.querySelectorAll('p'))
          .slice(1)
          .map((p) => p.textContent)
      )
    ).toEqual([
      ['  De\u0301cision : ne pas payer.\n', 'Montant : 250 €.  '],
      ['  Reconnexion.\n', 'Ne pas confirmer.  '],
    ])
    expect(screen.getByRole('region', { name: 'Groupe source 1' })).toBeTruthy()
    expect(screen.getByRole('region', { name: 'Groupe source 2' })).toBeTruthy()
    expect(screen.getByText(fr.transcript.native.localTimes)).toBeTruthy()
    expect(screen.getByText(fr.transcript.partial)).toBeTruthy()
    expect(screen.getAllByRole('status')[0].textContent).toBe(
      fr.status.transcript.partial
    )
    expect(document.body.textContent).not.toContain(epochA)
    expect(document.body.textContent).not.toContain(epochB)
    expect(meeting.transcript.status).toBe('partial')
  })

  it.each(['fr', 'en'] as const)(
    'copies source groups with explicit local times in %s',
    async (locale) => {
      const copy = vi.fn().mockResolvedValue(undefined)
      vi.stubGlobal('navigator', { clipboard: { writeText: copy } })
      const translations = { fr, en }[locale]
      const localized = createInstance()
      await localized.init({
        lng: locale,
        resources: { [locale]: { meetingHistory: translations } },
        initAsync: false,
      })
      const meeting = detail({
        transcript_projection: projection('partial'),
        transcript: {
          segments: [
            {
              id: 'same-id',
              start_ms: 5000,
              speaker: 'Camille',
              text: '  De\u0301cision : ne pas payer.\n',
              source_epoch: {
                epoch_ref: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
                time_basis: 'source_relative_ms',
              },
            },
            {
              id: 'next',
              start_ms: 6000,
              speaker: 'Camille',
              text: 'Montant : 250 €.  ',
              source_epoch: {
                epoch_ref: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
                time_basis: 'source_relative_ms',
              },
            },
            {
              id: 'same-id',
              start_ms: 0,
              speaker: 'Camille',
              text: '  Reconnexion.\n',
              source_epoch: {
                epoch_ref: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
                time_basis: 'source_relative_ms',
              },
            },
            {
              id: 'other',
              start_ms: 2000,
              speaker: null,
              text: 'Ne pas confirmer.  ',
              source_epoch: {
                epoch_ref: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
                time_basis: 'source_relative_ms',
              },
            },
          ],
        },
      })
      render(
        <I18nextProvider i18n={localized}>
          <MeetingTranscriptSection transcript={meeting.transcript} />
        </I18nextProvider>
      )
      fireEvent.click(
        screen.getByRole('button', { name: translations.content.copy })
      )
      const group = (number: number) =>
        translations.transcript.native.source.replace(
          '{{number}}',
          String(number)
        )
      const time = (value: string) =>
        translations.transcript.native.localTime.replace('{{time}}', value)
      await waitFor(() =>
        expect(copy).toHaveBeenCalledWith(
          [
            translations.transcript.native.localTimes,
            group(1),
            `Camille (${time('00:05')})\n  De\u0301cision : ne pas payer.\n\nMontant : 250 €.  `,
            group(2),
            `Camille (${time('00:00')})\n  Reconnexion.\n`,
            `${translations.transcript.unknownSpeaker} (${time('00:02')})\nNe pas confirmer.  `,
          ].join('\n\n')
        )
      )
      expect(
        screen
          .getAllByRole('heading', { level: 3 })
          .map((heading) => heading.textContent)
      ).toEqual([group(1), group(2)])
      expect(
        within(screen.getByRole('region', { name: group(2) })).getAllByRole(
          'listitem'
        )
      ).toHaveLength(2)
    }
  )

  it('keeps one chronological turn and the copy format for a classic provider speaker', async () => {
    const copy = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText: copy } })
    renderTranscript(
      detail({
        transcript_projection: projection('available'),
        transcript: {
          segments: [
            { id: 's2', start_ms: 6000, speaker: 'Camille', text: 'Après.' },
            { id: 's1', start_ms: 0, speaker: 'Camille', text: 'Avant.' },
          ],
        },
      })
    )

    const turns = screen.getAllByRole('listitem')
    expect(turns).toHaveLength(1)
    expect(turns[0].querySelector('p')?.textContent).toBe('Camille00:00')
    expect(
      Array.from(turns[0].querySelectorAll('p'))
        .slice(1)
        .map((p) => p.textContent)
    ).toEqual(['Avant.', 'Après.'])
    expect(screen.queryByText(fr.transcript.native.localTimes)).toBeNull()
    expect(screen.queryByRole('heading', { level: 3 })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Copier' }))
    await waitFor(() =>
      expect(copy).toHaveBeenCalledWith('Camille (00:00)\nAvant.\nAprès.')
    )
  })

  it('preserves input order and does not invent a clock basis for an unspecified source', async () => {
    const copy = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText: copy } })
    const meeting = detail({ transcript_projection: projection('partial') })
    meeting.transcript.segments = [
      {
        id: 's1',
        startMs: 6000,
        speaker: 'A',
        text: 'Premier.',
        sourceEpoch: { epochRef: 'source-a', timeBasis: 'source_relative_ms' },
      },
      { id: 's2', startMs: 1000, speaker: 'A', text: 'Sans source.' },
      {
        id: 's1',
        startMs: 0,
        speaker: 'B',
        text: 'Deuxième.',
        sourceEpoch: { epochRef: 'source-b', timeBasis: 'source_relative_ms' },
      },
      {
        id: 's3',
        startMs: 8000,
        speaker: 'A',
        text: 'Retour.',
        sourceEpoch: { epochRef: 'source-a', timeBasis: 'source_relative_ms' },
      },
    ]
    meeting.transcript.truncated = true
    renderTranscript(meeting)

    expect(
      screen
        .getAllByRole('heading', { level: 3 })
        .map((heading) => heading.textContent)
    ).toEqual([
      'Groupe source 1',
      'Source non précisée',
      'Groupe source 2',
      'Groupe source 1',
    ])
    expect(
      screen
        .getAllByRole('listitem')
        .map((turn) => turn.querySelectorAll('p')[1].textContent)
    ).toEqual(['Premier.', 'Sans source.', 'Deuxième.', 'Retour.'])
    expect(screen.getByText(fr.transcript.native.unknownTimeBasis)).toBeTruthy()
    expect(screen.getByText(fr.transcript.truncated)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Copier' }))
    await waitFor(() => expect(copy).toHaveBeenCalledOnce())
    expect(copy.mock.calls[0][0]).toContain(
      'Source non précisée\n\nA (Repère temporel non précisé)\nSans source.'
    )
    expect(copy.mock.calls[0][0]).not.toContain('00:01')
  })
})

describe('native coverage in history', () => {
  it.each(['partial', 'available', 'transcribing', 'failed'] as const)(
    'shows source uncertainty without changing the %s transcript status',
    (status) => {
      const meeting = detail({ transcript_projection: projection(status) })
      meeting.transcript.nativeCoverage = {
        meetingCoverage: 'unverified',
        observationsAvailable: true,
        truncated: false,
        tracks: [
          {
            observationRef: 'private-observation',
            captureState: 'admitted',
            epochRefs: ['private-epoch'],
          },
        ],
        epochs: [{ epochRef: 'private-epoch', state: 'pending' }],
      }
      renderTranscript(meeting)
      expect(
        screen.getByRole('region', {
          name: fr.transcript.native.coverage.title,
        })
      ).toBeTruthy()
      expect(
        screen.getByText(fr.transcript.native.coverage.unverified)
      ).toBeTruthy()
      expect(
        screen.getByText(fr.transcript.native.coverage.pending)
      ).toBeTruthy()
      expect(screen.getAllByRole('status')[0].textContent).toBe(
        fr.status.transcript[status]
      )
      expect(meeting.transcript.status).toBe(status)
    }
  )

  it('includes unverified coverage in copied text and clears the indication when metadata disappears', async () => {
    const copy = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText: copy } })
    const meeting = detail({
      transcript_projection: projection('partial'),
      transcript: {
        segments: [
          { id: 's1', start_ms: 0, speaker: 'Camille', text: 'Conservé.' },
        ],
      },
    })
    meeting.transcript.segments[0].sourceEpoch = {
      epochRef: 'native-epoch',
      timeBasis: 'source_relative_ms',
    }
    meeting.transcript.nativeCoverage = {
      meetingCoverage: 'unverified',
      observationsAvailable: false,
      truncated: false,
      tracks: [],
      epochs: [],
    }
    const view = renderTranscript(meeting)
    fireEvent.click(screen.getByRole('button', { name: 'Copier' }))
    await waitFor(() =>
      expect(copy).toHaveBeenCalledWith(
        `${fr.transcript.native.coverage.unverified}\n\n${fr.transcript.native.localTimes}\n\nGroupe source 1\n\nCamille (Temps local 00:00)\nConservé.`
      )
    )
    const { nativeCoverage: _coverage, ...withoutCoverage } = meeting.transcript
    view.rerender(
      <I18nextProvider i18n={i18n}>
        <MeetingTranscriptSection transcript={withoutCoverage} />
      </I18nextProvider>
    )
    expect(
      screen.queryByRole('region', {
        name: fr.transcript.native.coverage.title,
      })
    ).toBeNull()
    expect(screen.getByText('Conservé.')).toBeTruthy()
  })

  it('keeps canonical provider copy unchanged with secondary native coverage and retained live content', async () => {
    const copy = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText: copy } })
    const meeting = detail({
      transcript_projection: projection('available'),
      transcript: {
        segments: [
          {
            id: 's1',
            start_ms: 4000,
            speaker: 'Camille',
            text: 'Texte canonique.',
          },
        ],
      },
    })
    meeting.transcript.nativeCoverage = {
      meetingCoverage: 'unverified',
      observationsAvailable: true,
      truncated: false,
      tracks: [
        {
          observationRef: 'secondary-source',
          captureState: 'unadmitted',
          epochRefs: [],
        },
      ],
      epochs: [],
    }
    meeting.transcript.liveSources = [
      {
        sourceRef: 'retained-source',
        participantKind: 'guest',
        participantRef: 'participant',
        state: 'ended',
        incomplete: true,
        confirmedSequence: 1,
        events: [
          {
            version: 2,
            source_ref: 'retained-source',
            leg_ref: 'retained-leg',
            sequence: 1,
            kind: 'final',
            item_ref: 'retained-item',
            revision: 1,
            text: '  Texte live secondaire.\n',
            language: 'fr',
            timing: { state: 'unavailable' },
          },
        ],
      },
    ]
    renderTranscript(meeting)

    expect(
      screen.getByText(fr.transcript.native.coverage.unverified)
    ).toBeTruthy()
    expect(screen.queryByText(fr.transcript.native.localTimes)).toBeNull()
    const retained = screen.getByRole('region', {
      name: fr.transcript.live.title,
    })
    expect(within(retained).getByText(fr.transcript.live.partial)).toBeTruthy()
    expect(retained.querySelector('li > p')?.textContent).toBe(
      '  Texte live secondaire.\n'
    )
    expect(screen.getAllByRole('status')[0].textContent).toBe(
      fr.status.transcript.available
    )
    fireEvent.click(screen.getByRole('button', { name: 'Copier' }))
    await waitFor(() =>
      expect(copy).toHaveBeenCalledWith('Camille (00:04)\nTexte canonique.')
    )
  })
})

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

  it('keeps native coverage visible during suspension without live content or a duplicate processing panel', () => {
    const meeting = detail()
    meeting.transcript.projection.processing_state = 'temporarily_suspended'
    meeting.transcript.nativeCoverage = {
      meetingCoverage: 'unverified',
      observationsAvailable: true,
      truncated: false,
      tracks: [
        {
          observationRef: 'source-pending',
          captureState: 'admitted',
          epochRefs: ['epoch-pending'],
        },
      ],
      epochs: [{ epochRef: 'epoch-pending', state: 'pending' }],
    }
    renderTranscript(meeting)

    expect(screen.getByRole('status').textContent).toBe(suspendedTitle)
    expect(screen.getByText(thresholdExplanation)).toBeTruthy()
    expect(
      screen.getByRole('region', { name: fr.transcript.native.coverage.title })
    ).toBeTruthy()
    expect(
      screen.getByText(fr.transcript.native.coverage.unverified)
    ).toBeTruthy()
    expect(screen.getByText(fr.transcript.native.coverage.pending)).toBeTruthy()
    expect(screen.queryByText(fr.transcript.transcribing.title)).toBeNull()
    expect(
      screen.queryByText(fr.transcript.transcribing.description)
    ).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
    expect(meeting.transcript.status).toBe('transcribing')
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
