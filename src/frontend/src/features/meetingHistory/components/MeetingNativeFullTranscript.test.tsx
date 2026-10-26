import {
  act,
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
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import en from '@/locales/en/meetingHistory.json'
import fr from '@/locales/fr/meetingHistory.json'
import {
  mergeHistoryDetail,
  normalizeHistoryDetail,
} from '../api/meetingHistoryApi'
import {
  fullCoverage,
  fullDigest,
  fullEpochA,
  fullEpochB,
  fullHistoryDetail,
  fullMeetingRef,
  fullResponse,
  fullSegment,
} from '../api/meetingNativeFull.fixtures'
import type { MeetingHistoryDetail } from '../api/types'
import { MeetingTranscriptSection } from './MeetingTranscriptSection'

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
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
    isDisabled,
  }: {
    children: ReactNode
    onPress?: () => void
    isDisabled?: boolean
  }) => (
    <button type="button" onClick={onPress} disabled={isDisabled}>
      {children}
    </button>
  ),
}))
beforeEach(() => {
  vi.mocked(fetchApi).mockReset()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const detail = (changes: Record<string, unknown> = {}) => {
  const meeting = normalizeHistoryDetail(fullHistoryDetail(changes))
  if (!meeting) throw new Error('Expected retained native history fixture')
  return meeting
}
const renderDetail = (meeting = detail(), language = 'fr') => {
  const i18n = createInstance()
  void i18n.init({
    lng: language,
    initAsync: false,
    resources: { fr: { meetingHistory: fr }, en: { meetingHistory: en } },
  })
  const view = (next: MeetingHistoryDetail) => (
    <I18nextProvider i18n={i18n}>
      <MeetingTranscriptSection
        transcript={next.transcript}
        meetingRef={next.id}
      />
    </I18nextProvider>
  )
  const result = render(view(meeting))
  return {
    ...result,
    refresh: (next: MeetingHistoryDetail) => result.rerender(view(next)),
  }
}
const clickLoad = () =>
  fireEvent.click(
    screen.getByRole('button', { name: fr.transcript.native.full.load })
  )
const loaded = () =>
  screen.findByRole('region', { name: fr.transcript.native.full.title })

describe('retained native full consultation in the real transcript section', () => {
  it.each([
    ['fr', fr],
    ['en', en],
  ] as const)(
    '%s loads and copies the 1001st segment and repeated local IDs in separate epochs',
    async (language, messages) => {
      const first = Array.from({ length: 1001 }, (_, index) =>
        fullSegment({
          id: `${fullEpochA}_s${index + 1}`,
          start_ms: 5000 + index * 1000,
          text: `  Passage ${index}.\n`,
        })
      )
      first[0] = fullSegment()
      const second = fullResponse().segments[1]
      vi.mocked(fetchApi).mockResolvedValue(
        fullResponse({ segments: [...first, second] })
      )
      const writeText = vi.fn().mockResolvedValue(undefined)
      vi.stubGlobal('navigator', { clipboard: { writeText } })
      const meeting = detail()
      const before = JSON.stringify(meeting)
      renderDetail(meeting, language)
      expect(fetchApi).not.toHaveBeenCalled()
      fireEvent.click(
        screen.getByRole('button', {
          name: messages.transcript.native.full.load,
        })
      )
      const full = await screen.findByRole('region', {
        name: messages.transcript.native.full.title,
      })
      expect(within(full).getByText('Passage 1000.').textContent).toBe(
        first[1000].text
      )
      expect(screen.getAllByText(/ne pas payer/)).toHaveLength(1)
      expect(
        screen.getAllByRole('button', { name: messages.content.copy })
      ).toHaveLength(1)
      expect(screen.queryByText(messages.transcript.truncated)).toBeNull()
      expect(
        screen.getByText(messages.transcript.native.coverage.failed)
      ).toBeTruthy()
      expect(
        screen.getByText(messages.transcript.native.coverage.unverified)
      ).toBeTruthy()
      const localTime = (time: string) =>
        messages.transcript.native.localTime.replace('{{time}}', time)
      const source = (number: number) =>
        messages.transcript.native.source.replace('{{number}}', String(number))
      expect(within(full).getByText(localTime('00:05'))).toBeTruthy()
      expect(within(full).getByText(localTime('00:00'))).toBeTruthy()
      fireEvent.click(
        within(full).getByRole('button', { name: messages.content.copy })
      )
      const exact = [
        messages.transcript.native.full.scope,
        messages.transcript.native.coverage.unverified,
        messages.transcript.native.localTimes,
        source(1),
        `Camille (${localTime('00:05')})\n${first.map((segment) => segment.text).join('\n')}`,
        source(2),
        `Camille (${localTime('00:00')})\n${second.text}`,
      ].join('\n\n')
      await waitFor(() => expect(writeText).toHaveBeenCalledWith(exact))
      expect(fetchApi).toHaveBeenCalledExactlyOnceWith(
        `meetings/history/${fullMeetingRef}/native-transcript/?snapshot_digest=${fullDigest}`,
        { cache: 'no-store', signal: expect.any(AbortSignal) }
      )
      expect(JSON.stringify(meeting)).toBe(before)
    }
  )

  it.each([401, 403, 404, 409, 410, 422, 503])(
    'keeps the preview after HTTP %s and retries only on explicit request',
    async (status) => {
      vi.mocked(fetchApi)
        .mockRejectedValueOnce(new ApiError(status, {}))
        .mockResolvedValueOnce(fullResponse())
      renderDetail()
      clickLoad()
      expect(await screen.findByRole('alert')).toBeTruthy()
      expect(
        screen.queryByRole('region', { name: fr.transcript.native.full.title })
      ).toBeNull()
      expect(screen.getByText(fr.transcript.truncated)).toBeTruthy()
      expect(screen.getByText(/ne pas payer/)).toBeTruthy()
      expect(fetchApi).toHaveBeenCalledTimes(1)
      fireEvent.click(
        screen.getByRole('button', { name: fr.transcript.native.full.retry })
      )
      await loaded()
      expect(screen.queryByRole('alert')).toBeNull()
      expect(fetchApi).toHaveBeenCalledTimes(2)
    }
  )

  it('rejects invalid loaded content, retains the preview and does not store it canonically', async () => {
    vi.mocked(fetchApi).mockResolvedValue(
      fullResponse({ snapshot_digest: 'f'.repeat(64) })
    )
    const meeting = detail()
    const before = JSON.stringify(meeting)
    renderDetail(meeting)
    clickLoad()
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.queryByText(fr.transcript.native.full.scope)).toBeNull()
    expect(JSON.stringify(meeting)).toBe(before)
  })

  it.each([
    { native_coverage: undefined },
    { native_coverage: null },
    { native_coverage: fullCoverage({ snapshot_digest: 'invalid' }) },
    {
      native_coverage: fullCoverage({
        epochs: [{ epoch_ref: fullEpochA, state: 'pending' }],
      }),
    },
    { transcript: null },
    {
      transcript: {
        status: 'available',
        segments: [fullSegment(), {}],
        truncated: true,
      },
    },
  ])(
    'withdraws the full view on absent/invalid/pending fresh coverage or body: %j',
    async (changes) => {
      vi.mocked(fetchApi).mockResolvedValue(fullResponse())
      const current = detail()
      const { refresh } = renderDetail(current)
      clickLoad()
      await loaded()
      const incoming = detail({ ...changes })
      refresh(mergeHistoryDetail(current, incoming))
      expect(
        screen.queryByRole('region', { name: fr.transcript.native.full.title })
      ).toBeNull()
      expect(screen.queryByText(/Reconnexion/)).toBeNull()
      expect(
        screen.queryByRole('button', { name: fr.transcript.native.full.load })
      ).toBeNull()
    }
  )

  it('withdraws expansion on an absent fresh body even if the canonical cache retains it', async () => {
    vi.mocked(fetchApi).mockResolvedValue(fullResponse())
    const current = detail()
    const { refresh } = renderDetail(current)
    clickLoad()
    await loaded()
    const incoming = detail({
      transcript_projection: {
        version: 1,
        state: 'unknown',
        revision: 2,
        digest: null,
        source: null,
      },
      transcript: null,
    })
    const merged = mergeHistoryDetail(current, incoming)
    expect(merged.transcript.segments[0].text).toBe(
      current.transcript.segments[0].text
    )
    expect(merged.transcript.nativeFullSnapshot).toBeUndefined()
    refresh(merged)
    expect(
      screen.queryByRole('region', { name: fr.transcript.native.full.title })
    ).toBeNull()
    expect(screen.getByText(/ne pas payer/)).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: fr.transcript.native.full.load })
    ).toBeNull()
  })

  it('resets a loaded consultation when the body identity changes at the same revision and digest', async () => {
    vi.mocked(fetchApi).mockResolvedValue(fullResponse())
    const current = detail()
    const { refresh } = renderDetail(current)
    clickLoad()
    await loaded()
    refresh(
      mergeHistoryDetail(
        current,
        detail({
          transcript: {
            status: 'available',
            segments: [fullSegment({ text: '  Aperçu corrigé.\n' })],
            truncated: true,
          },
        })
      )
    )
    expect(
      screen.queryByRole('region', { name: fr.transcript.native.full.title })
    ).toBeNull()
    expect(screen.getByText('Aperçu corrigé.')).toBeTruthy()
    expect(
      screen.getByRole('button', { name: fr.transcript.native.full.load })
    ).toBeTruthy()
    expect(fetchApi).toHaveBeenCalledTimes(1)
  })

  it('withdraws loaded content when a fresh speaker changes only in exact spacing', async () => {
    vi.mocked(fetchApi).mockResolvedValue(fullResponse())
    const current = detail()
    const { refresh } = renderDetail(current)
    clickLoad()
    await loaded()
    const incoming = detail({
      transcript: {
        status: 'available',
        segments: [fullSegment({ speaker: '  Camille  ' })],
        truncated: true,
      },
    })
    // Canonical normalization trims display names; the fresh native body still changed.
    expect(incoming.transcript.segments).toEqual(current.transcript.segments)
    refresh(mergeHistoryDetail(current, incoming))
    expect(
      screen.queryByRole('region', { name: fr.transcript.native.full.title })
    ).toBeNull()
    expect(
      screen.getByRole('button', { name: fr.transcript.native.full.load })
    ).toBeTruthy()
    expect(fetchApi).toHaveBeenCalledTimes(1)
  })

  it('resets on changed coverage facts without changing the canonical projection', async () => {
    vi.mocked(fetchApi).mockResolvedValue(fullResponse())
    const current = detail()
    const { refresh } = renderDetail(current)
    clickLoad()
    await loaded()
    const coverage = fullCoverage({
      epochs: [
        { epoch_ref: fullEpochA, state: 'available' },
        { epoch_ref: fullEpochB, state: 'failed' },
      ],
    })
    refresh(mergeHistoryDetail(current, detail({ native_coverage: coverage })))
    expect(
      screen.queryByRole('region', { name: fr.transcript.native.full.title })
    ).toBeNull()
    expect(
      screen.getByRole('button', { name: fr.transcript.native.full.load })
    ).toBeTruthy()
    expect(fetchApi).toHaveBeenCalledTimes(1)
  })

  it.each([-1, 0.5, null, '1', undefined])(
    'refuses a malformed raw revision before canonical normalization: %s',
    (revision) => {
      const meeting = detail({
        transcript_projection: {
          ...fullHistoryDetail().transcript_projection,
          revision,
        },
      })
      expect(meeting.transcript.projection.revision).toBe(0)
      expect(meeting.transcript.nativeFullSnapshot).toBeUndefined()
      renderDetail(meeting)
      expect(
        screen.queryByRole('button', { name: fr.transcript.native.full.load })
      ).toBeNull()
      expect(fetchApi).not.toHaveBeenCalled()
    }
  )

  it.each(['loaded', 'in_flight'] as const)(
    'withdraws %s consultation when valid revision zero is followed by an invalid raw revision',
    async (phase) => {
      let finish!: (value: unknown) => void
      if (phase === 'in_flight') {
        vi.mocked(fetchApi).mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              finish = resolve
            })
        )
      } else {
        vi.mocked(fetchApi).mockResolvedValueOnce(fullResponse())
      }
      const projection = {
        ...fullHistoryDetail().transcript_projection,
        revision: 0,
      }
      const current = detail({ transcript_projection: projection })
      const { refresh } = renderDetail(current)
      clickLoad()
      if (phase === 'loaded') await loaded()
      const signal = vi.mocked(fetchApi).mock.calls[0][1]?.signal
      refresh(
        mergeHistoryDetail(
          current,
          detail({ transcript_projection: { ...projection, revision: -1 } })
        )
      )
      if (phase === 'in_flight') {
        expect(signal?.aborted).toBe(true)
        await act(async () => finish(fullResponse()))
      }
      expect(
        screen.queryByRole('region', { name: fr.transcript.native.full.title })
      ).toBeNull()
      expect(
        screen.queryByRole('button', { name: fr.transcript.native.full.load })
      ).toBeNull()
      expect(fetchApi).toHaveBeenCalledTimes(1)
    }
  )

  it.each(['loaded', 'in_flight'] as const)(
    'offers an explicit read of a newer snapshot while the %s canonical revision stays immutable',
    async (phase) => {
      let finish!: (value: unknown) => void
      if (phase === 'in_flight') {
        vi.mocked(fetchApi).mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              finish = resolve
            })
        )
      } else {
        vi.mocked(fetchApi).mockResolvedValueOnce(fullResponse())
      }
      const current = detail()
      const { refresh } = renderDetail(current)
      clickLoad()
      if (phase === 'loaded') await loaded()
      const signal = vi.mocked(fetchApi).mock.calls[0][1]?.signal
      const nextDigest = 'f'.repeat(64)
      const incoming = detail({
        transcript_projection: {
          ...fullHistoryDetail().transcript_projection,
          digest: nextDigest,
        },
        native_coverage: fullCoverage({ snapshot_digest: nextDigest }),
      })
      const merged = mergeHistoryDetail(current, incoming)
      expect(merged.transcript.projection.digest).toBe(fullDigest)
      expect(merged.transcript.projection.refreshPending).toBe(true)
      expect(merged.transcript.segments).toBe(current.transcript.segments)
      refresh(merged)
      if (phase === 'in_flight') {
        expect(signal?.aborted).toBe(true)
        await act(async () => finish(fullResponse()))
      }
      expect(
        screen.queryByRole('region', { name: fr.transcript.native.full.title })
      ).toBeNull()
      expect(fetchApi).toHaveBeenCalledTimes(1)
      vi.mocked(fetchApi).mockResolvedValueOnce(
        fullResponse({
          snapshot_digest: nextDigest,
          native_coverage: fullCoverage({ snapshot_digest: nextDigest }),
        })
      )
      clickLoad()
      await loaded()
      expect(vi.mocked(fetchApi).mock.calls[1][0]).toContain(
        `snapshot_digest=${nextDigest}`
      )
      expect(fetchApi).toHaveBeenCalledTimes(2)
    }
  )

  it('retains the loaded consultation on a fresh confirmation of exactly the same body and coverage', async () => {
    vi.mocked(fetchApi).mockResolvedValue(fullResponse())
    const current = detail()
    const { refresh } = renderDetail(current)
    clickLoad()
    await loaded()
    refresh(mergeHistoryDetail(current, detail()))
    expect(
      screen.getByRole('region', { name: fr.transcript.native.full.title })
    ).toBeTruthy()
    expect(fetchApi).toHaveBeenCalledTimes(1)
  })

  it.each(['meeting', 'digest'])(
    'removes loaded text when the %s identity changes',
    async (kind) => {
      vi.mocked(fetchApi).mockResolvedValue(fullResponse())
      const current = detail()
      const { refresh } = renderDetail(current)
      clickLoad()
      await loaded()
      let next = detail({
        id: 'meeting_another0123456789',
        native_coverage: fullCoverage({
          meeting_ref: 'meeting_another0123456789',
        }),
      })
      if (kind === 'digest') {
        next = detail({
          transcript_projection: {
            ...fullHistoryDetail().transcript_projection,
            digest: 'f'.repeat(64),
          },
          native_coverage: fullCoverage({ snapshot_digest: 'f'.repeat(64) }),
        })
      }
      refresh(next)
      expect(
        screen.queryByRole('region', { name: fr.transcript.native.full.title })
      ).toBeNull()
      expect(
        screen.getByRole('button', { name: fr.transcript.native.full.load })
      ).toBeTruthy()
      expect(fetchApi).toHaveBeenCalledTimes(1)
    }
  )

  it('cannot resurrect a withdrawn view from a late response, even when the old identity returns', async () => {
    let finish!: (value: unknown) => void
    vi.mocked(fetchApi).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    const current = detail()
    const { refresh } = renderDetail(current)
    clickLoad()
    const pendingSignal = vi.mocked(fetchApi).mock.calls[0][1]?.signal
    expect(
      screen
        .getByRole('button', { name: fr.transcript.native.full.loading })
        .hasAttribute('disabled')
    ).toBe(true)
    refresh(detail({ native_coverage: null }))
    expect(pendingSignal?.aborted).toBe(true)
    refresh(current)
    await act(async () => finish(fullResponse()))
    expect(
      screen.queryByRole('region', { name: fr.transcript.native.full.title })
    ).toBeNull()
    expect(
      screen.getByRole('button', { name: fr.transcript.native.full.load })
    ).toBeTruthy()
    expect(fetchApi).toHaveBeenCalledTimes(1)
  })

  it('clears the loaded text and copy target at retention expiry using integer seconds', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-25T12:00:00Z'))
    vi.mocked(fetchApi).mockResolvedValue(
      fullResponse({ retention_expires_at: Date.now() / 1000 + 2 })
    )
    renderDetail()
    await act(async () => clickLoad())
    expect(
      screen.getByRole('region', { name: fr.transcript.native.full.title })
    ).toBeTruthy()
    act(() => vi.advanceTimersByTime(2000))
    expect(
      screen.queryByRole('region', { name: fr.transcript.native.full.title })
    ).toBeNull()
    expect(screen.queryByText(/Reconnexion/)).toBeNull()
    expect(screen.getByText(fr.transcript.native.full.expired)).toBeTruthy()
    expect(
      screen.getByRole('button', { name: fr.transcript.native.full.retry })
    ).toBeTruthy()
    expect(screen.getByText(fr.transcript.truncated)).toBeTruthy()
  })

  it('keeps long retention times without overflowing the browser timer', async () => {
    vi.useFakeTimers()
    vi.mocked(fetchApi).mockResolvedValue(
      fullResponse({
        retention_expires_at: Math.floor(Date.now() / 1000) + 4_000_000,
      })
    )
    renderDetail()
    await act(async () => clickLoad())
    act(() => vi.advanceTimersByTime(1))
    expect(
      screen.getByRole('region', { name: fr.transcript.native.full.title })
    ).toBeTruthy()
  })

  it('does not retain loaded text across component unmounts', async () => {
    vi.mocked(fetchApi).mockResolvedValue(fullResponse())
    const first = renderDetail()
    clickLoad()
    await loaded()
    first.unmount()
    renderDetail()
    expect(
      screen.queryByRole('region', { name: fr.transcript.native.full.title })
    ).toBeNull()
    expect(
      screen.getByRole('button', { name: fr.transcript.native.full.load })
    ).toBeTruthy()
    expect(fetchApi).toHaveBeenCalledTimes(1)
  })
})

it.each([
  ['epoch_limit_exceeded', 'loaded'],
  ['epoch_limit_exceeded', 'in_flight'],
  ['result_bytes_limit_exceeded', 'loaded'],
  ['result_bytes_limit_exceeded', 'in_flight'],
] as const)(
  'withdraws %s full consultation (%s) while keeping canonical cached text',
  async (reason, phase) => {
    let finish!: (value: unknown) => void
    if (phase === 'in_flight') {
      vi.mocked(fetchApi).mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve
          })
      )
    } else {
      vi.mocked(fetchApi).mockResolvedValueOnce(fullResponse())
    }
    const current = detail()
    const { refresh } = renderDetail(current)
    clickLoad()
    if (phase === 'loaded') await loaded()
    const signal = vi.mocked(fetchApi).mock.calls[0][1]?.signal
    const incoming = detail({
      transcript_projection: {
        ...fullHistoryDetail().transcript_projection,
        revision: 0,
      },
      native_coverage: fullCoverage({
        epochs_unavailable_reason: reason,
        epochs: [],
      }),
    })
    const merged = mergeHistoryDetail(current, incoming)
    expect(merged.transcript.segments).toEqual(current.transcript.segments)
    expect(merged.transcript.nativeFullSnapshot).toBeUndefined()
    expect(merged.transcript.nativePartial).toBeUndefined()
    expect(merged.transcript.nativeCoverage?.epochsUnavailableReason).toBe(
      reason
    )
    refresh(merged)
    if (phase === 'in_flight') {
      expect(signal?.aborted).toBe(true)
      await act(async () => finish(fullResponse()))
    }
    expect(
      screen.queryByRole('region', { name: fr.transcript.native.full.title })
    ).toBeNull()
    expect(
      screen.queryByRole('button', { name: fr.transcript.native.full.load })
    ).toBeNull()
    expect(screen.queryByText(/Reconnexion/)).toBeNull()
    expect(screen.getByText(/ne pas payer/)).toBeTruthy()
    expect(screen.getByText(fr.transcript.native.coverage[reason])).toBeTruthy()
    expect(fetchApi).toHaveBeenCalledTimes(1)
  }
)
