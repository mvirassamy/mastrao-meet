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
import en from '@/locales/en/meetingHistory.json'
import fr from '@/locales/fr/meetingHistory.json'
import {
  mergeHistoryDetail,
  normalizeHistoryDetail,
} from '../api/meetingHistoryApi'
import {
  nativeCoverage,
  nativeDetail,
  nativeDigest,
  nativeEpochA,
  nativeEpochB,
  nativePartial,
  nativeProjection,
  nativeSegment,
} from '../api/meetingNativePartial.fixtures'
import type { MeetingHistoryDetail } from '../api/types'
import { MeetingTranscriptSection } from './MeetingTranscriptSection'

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

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const detail = (changes: Record<string, unknown> = {}) => {
  const result = normalizeHistoryDetail(nativeDetail(changes))
  if (!result) throw new Error('Expected native history fixture')
  return result
}

const renderDetail = (meeting: MeetingHistoryDetail, language = 'fr') => {
  const i18n = createInstance()
  void i18n.init({
    lng: language,
    resources: { fr: { meetingHistory: fr }, en: { meetingHistory: en } },
    initAsync: false,
  })
  const view = (next: MeetingHistoryDetail) => (
    <I18nextProvider i18n={i18n}>
      <MeetingTranscriptSection transcript={next.transcript} />
    </I18nextProvider>
  )
  const result = render(view(meeting))
  return {
    ...result,
    refresh: (next: MeetingHistoryDetail) => result.rerender(view(next)),
  }
}

describe('native partial consultation', () => {
  it.each([
    ['fr', fr],
    ['en', en],
  ] as const)(
    '%s renders and copies exact partial text with local times, pending/failure and truncation',
    async (language, messages) => {
      const writeText = vi.fn().mockResolvedValue(undefined)
      vi.stubGlobal('navigator', { clipboard: { writeText } })
      const second = nativeSegment({
        source_epoch: {
          epoch_ref: nativeEpochB,
          time_basis: 'source_relative_ms',
        },
        start_ms: 0,
        text: '  Reconnexion.\nNe pas confirmer.  ',
      })
      const meeting = detail({
        native_coverage: nativeCoverage({
          epochs: [
            { epoch_ref: nativeEpochA, state: 'available' },
            { epoch_ref: nativeEpochB, state: 'available' },
            {
              epoch_ref: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
              state: 'pending',
            },
            {
              epoch_ref: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
              state: 'failed',
            },
          ],
        }),
        native_partial: nativePartial({
          segments: [nativeSegment(), second],
          truncated: true,
        }),
      })
      renderDetail(meeting, language)
      const region = screen.getByRole('region', {
        name: messages.transcript.native.partialTitle,
      })
      const sourceOne = within(region).getByRole('region', {
        name: messages.transcript.native.source.replace('{{number}}', '1'),
      })
      const sourceTwo = within(region).getByRole('region', {
        name: messages.transcript.native.source.replace('{{number}}', '2'),
      })
      const localTime = (time: string) =>
        messages.transcript.native.localTime.replace('{{time}}', time)
      expect(within(sourceOne).getByText(localTime('00:05'))).toBeTruthy()
      expect(within(sourceTwo).getByText(localTime('00:00'))).toBeTruthy()
      expect(sourceOne.querySelector('li > p + p')?.textContent).toBe(
        nativeSegment().text
      )
      expect(sourceTwo.querySelector('li > p + p')?.textContent).toBe(
        second.text
      )
      expect(
        screen.getByText(messages.transcript.transcribing.title)
      ).toBeTruthy()
      expect(
        screen.getByText(messages.transcript.native.coverage.pending)
      ).toBeTruthy()
      expect(
        screen.getByText(messages.transcript.native.coverage.failed)
      ).toBeTruthy()
      expect(
        within(region).getByText(messages.transcript.truncated)
      ).toBeTruthy()
      expect(meeting.transcript.segments).toEqual([])
      fireEvent.click(
        within(region).getByRole('button', { name: messages.content.copy })
      )
      const expected = [
        messages.transcript.native.partialNotice,
        messages.transcript.native.coverage.unverified,
        messages.transcript.native.localTimes,
        messages.transcript.native.source.replace('{{number}}', '1'),
        `  Camille   (${localTime('00:05')})\n${nativeSegment().text}`,
        messages.transcript.native.source.replace('{{number}}', '2'),
        `  Camille   (${localTime('00:00')})\n${second.text}`,
        messages.transcript.truncated,
      ].join('\n\n')
      await waitFor(() => expect(writeText).toHaveBeenCalledWith(expected))
    }
  )

  it.each([
    ['fr', fr],
    ['en', en],
  ] as const)(
    '%s keeps suspension visible alongside readable partial text',
    (language, messages) => {
      renderDetail(
        detail({
          transcript_projection: nativeProjection({
            processing_state: 'temporarily_suspended',
          }),
        }),
        language
      )
      expect(
        screen.getByRole('region', {
          name: messages.transcript.native.partialTitle,
        })
      ).toBeTruthy()
      expect(
        screen.getByText(messages.transcript.temporarilySuspended.title, {
          selector: 'p',
        })
      ).toBeTruthy()
      expect(
        screen.getByText(messages.transcript.temporarilySuspended.description)
      ).toBeTruthy()
      expect(
        screen.queryByText(messages.transcript.transcribing.title)
      ).toBeNull()
    }
  )

  it.each([
    [
      'unadmitted',
      {
        truncated: false,
        tracks: [
          {
            observation_ref: nativeEpochB,
            capture_state: 'unadmitted',
            epoch_refs: [],
          },
        ],
      },
    ],
    [
      'admitted without a confirmed epoch',
      {
        truncated: false,
        tracks: [
          {
            observation_ref: nativeEpochB,
            capture_state: 'admitted',
            epoch_refs: [],
          },
        ],
      },
    ],
    ['truncated observations', { truncated: true, tracks: [] }],
  ])(
    'keeps ready text and copy during pending processing with %s coverage',
    async (_kind, observations) => {
      const writeText = vi.fn().mockResolvedValue(undefined)
      vi.stubGlobal('navigator', { clipboard: { writeText } })
      const response = (changes: Record<string, unknown> = {}) =>
        detail({
          transcript_projection: nativeProjection({
            state: 'partial',
            digest: nativeDigest,
          }),
          native_coverage: nativeCoverage({
            track_observations: {
              meeting_coverage: 'unverified',
              ...observations,
            },
          }),
          ...changes,
        })
      let current = response()
      expect(current.transcript.nativePartial).toBeDefined()
      const { refresh } = renderDetail(current)
      const region = screen.getByRole('region', {
        name: fr.transcript.native.partialTitle,
      })
      expect(within(region).getByText(/ne pas payer/)).toBeTruthy()
      expect(screen.getByText(fr.transcript.transcribing.title)).toBeTruthy()
      fireEvent.click(
        within(region).getByRole('button', { name: fr.content.copy })
      )
      await waitFor(() =>
        expect(writeText).toHaveBeenCalledWith(
          expect.stringContaining(nativeSegment().text)
        )
      )
      current = mergeHistoryDetail(
        current,
        response({
          native_partial: nativePartial({
            segments: [
              nativeSegment({ text: '  Correction : ne pas payer 500 €.\n' }),
            ],
          }),
        })
      )
      refresh(current)
      expect(screen.getAllByText(/ne pas payer 500/)).toHaveLength(1)
      expect(screen.queryByText(/ne pas payer 250/)).toBeNull()
      current = mergeHistoryDetail(current, response({ native_partial: null }))
      refresh(current)
      expect(screen.queryByText(/ne pas payer/)).toBeNull()
      const final = response({
        transcript_projection: nativeProjection({
          state: 'partial',
          digest: 'b'.repeat(64),
        }),
        native_coverage: nativeCoverage({
          snapshot_digest: 'b'.repeat(64),
          epochs: [{ epoch_ref: nativeEpochA, state: 'available' }],
        }),
        transcript: {
          segments: [nativeSegment({ text: 'Résultat stabilisé.' })],
          truncated: false,
        },
        native_partial: null,
      })
      refresh(mergeHistoryDetail(current, final))
      expect(
        screen.queryByRole('region', {
          name: fr.transcript.native.partialTitle,
        })
      ).toBeNull()
      expect(screen.getAllByText('Résultat stabilisé.')).toHaveLength(1)
      expect(
        screen.getAllByRole('button', { name: fr.content.copy })
      ).toHaveLength(1)
    }
  )

  it.each([
    { revision: 2, pendingRevision: undefined, incomingRevision: 1 },
    { revision: 2, pendingRevision: 3, incomingRevision: 2 },
  ])(
    'never installs a first body rejected by revision bounds: %j',
    ({ revision, pendingRevision, incomingRevision }) => {
      const current = detail({
        transcript_projection: nativeProjection({
          state: 'partial',
          digest: nativeDigest,
          revision,
        }),
      })
      current.transcriptProjection.pendingRevision = pendingRevision
      const incoming = detail({
        transcript_projection: nativeProjection({
          state: 'partial',
          digest: 'b'.repeat(64),
          revision: incomingRevision,
        }),
        native_partial: null,
        transcript: {
          segments: [nativeSegment({ text: 'Corps périmé refusé.' })],
          truncated: false,
        },
      })
      const merged = mergeHistoryDetail(current, incoming)
      expect(merged.transcript.segments).toEqual([])
      expect(merged.transcriptProjection.digest).toBe(nativeDigest)
      renderDetail(merged)
      expect(screen.queryByText('Corps périmé refusé.')).toBeNull()
    }
  )

  it('replaces corrected text and newly available sources through repeated polls at the same revision', () => {
    let current = detail()
    const { refresh } = renderDetail(current)
    const corrected = detail({
      native_partial: nativePartial({
        segments: [nativeSegment({ text: '  Texte corrigé.\n' })],
      }),
    })
    current = mergeHistoryDetail(current, corrected)
    refresh(current)
    expect(screen.queryByText(/ne pas payer/)).toBeNull()
    expect(screen.getByText('Texte corrigé.')).toBeTruthy()
    const next = detail({
      native_coverage: nativeCoverage({
        epochs: [nativeEpochA, nativeEpochB].map((epoch_ref) => ({
          epoch_ref,
          state: 'available',
        })),
      }),
      native_partial: nativePartial({
        segments: [
          nativeSegment({ text: '  Texte corrigé.\n' }),
          nativeSegment({
            source_epoch: {
              epoch_ref: nativeEpochB,
              time_basis: 'source_relative_ms',
            },
            text: 'Nouvelle source.',
          }),
        ],
      }),
    })
    current = mergeHistoryDetail(current, next)
    refresh(current)
    expect(screen.getAllByText('Texte corrigé.')).toHaveLength(1)
    expect(screen.getAllByText('Nouvelle source.')).toHaveLength(1)
    expect(current.transcriptProjection.revision).toBe(1)
  })

  it.each([undefined, null, nativePartial({ coverage: 'complete' })])(
    'withdraws a fresh absent/null/invalid partial from display and copy: %j',
    (partial) => {
      const current = detail()
      const { refresh } = renderDetail(current)
      refresh(mergeHistoryDetail(current, detail({ native_partial: partial })))
      expect(
        screen.queryByRole('region', {
          name: fr.transcript.native.partialTitle,
        })
      ).toBeNull()
      expect(screen.queryByText(/ne pas payer/)).toBeNull()
      expect(screen.queryByRole('button', { name: fr.content.copy })).toBeNull()
      expect(screen.getByText(fr.transcript.transcribing.title)).toBeTruthy()
    }
  )

  it('stabilizes to the existing native canonical groups exactly once with the same revision', () => {
    const current = detail()
    const { refresh } = renderDetail(current)
    const settled = detail({
      transcript_projection: nativeProjection({
        state: 'partial',
        digest: nativeDigest,
      }),
      transcript: { segments: [nativeSegment()], truncated: false },
      native_partial: null,
    })
    const merged = mergeHistoryDetail(current, settled)
    refresh(merged)
    expect(
      screen.queryByRole('region', { name: fr.transcript.native.partialTitle })
    ).toBeNull()
    expect(screen.getAllByText(/ne pas payer/)).toHaveLength(1)
    expect(
      screen.getAllByRole('button', { name: fr.content.copy })
    ).toHaveLength(1)
    expect(merged.transcriptProjection.revision).toBe(1)
  })

  it('keeps cached provider/review content prioritary and clears fresh withdrawn native content', () => {
    const canonical = detail({
      transcript_projection: nativeProjection({
        state: 'available',
        revision: 5,
        digest: nativeDigest,
      }),
      transcript: {
        segments: [
          {
            id: 'review',
            start_ms: 1000,
            speaker: 'Review',
            text: 'Provider reviewed.',
          },
        ],
      },
    })
    const cached = mergeHistoryDetail(canonical, detail())
    expect(cached.transcript.nativePartial).toBeDefined()
    const { refresh } = renderDetail(cached)
    expect(
      screen.queryByRole('region', { name: fr.transcript.native.partialTitle })
    ).toBeNull()
    expect(screen.getByText('Provider reviewed.')).toBeTruthy()
    expect(screen.getByText('00:01')).toBeTruthy()
    const withdrawn = mergeHistoryDetail(
      cached,
      detail({ native_partial: null })
    )
    refresh(withdrawn)
    expect(screen.getByText('Provider reviewed.')).toBeTruthy()
    expect(withdrawn.transcript.nativePartial).toBeUndefined()
    expect(screen.queryByText(/ne pas payer/)).toBeNull()
    expect(
      screen.getAllByRole('button', { name: fr.content.copy })
    ).toHaveLength(1)
  })
  it('never reopens completed-empty content, even if an older auxiliary remains in cache', () => {
    const meeting = detail({
      transcript_projection: nativeProjection({ state: 'completed_empty' }),
    })
    meeting.transcript.nativePartial = detail().transcript.nativePartial
    renderDetail(meeting)
    expect(screen.getByText(fr.transcript.completed_empty.title)).toBeTruthy()
    expect(
      screen.queryByRole('region', { name: fr.transcript.native.partialTitle })
    ).toBeNull()
    expect(screen.queryByRole('button', { name: fr.content.copy })).toBeNull()
  })
})

it.each(['epoch_limit_exceeded', 'result_bytes_limit_exceeded'] as const)(
  'withdraws rendered partial text on fresh %s',
  (reason) => {
    const current = detail()
    const { refresh } = renderDetail(current)
    expect(
      screen.getByRole('region', { name: fr.transcript.native.partialTitle })
    ).toBeTruthy()
    const incoming = detail({
      native_coverage: nativeCoverage({
        epochs_unavailable_reason: reason,
        epochs: [],
      }),
      transcript_projection: nativeProjection({ state: 'audio_unavailable' }),
    })
    refresh(mergeHistoryDetail(current, incoming))
    expect(
      screen.queryByRole('region', { name: fr.transcript.native.partialTitle })
    ).toBeNull()
    expect(screen.queryByText(/ne pas payer/)).toBeNull()
    expect(screen.getByText(fr.transcript.native.coverage[reason])).toBeTruthy()
  }
)
