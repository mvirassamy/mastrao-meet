import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { createInstance } from 'i18next'
import type { ReactNode } from 'react'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import en from '@/locales/en/meetingHistory.json'
import fr from '@/locales/fr/meetingHistory.json'
import {
  mergeHistoryDetail,
  normalizeHistoryDetail,
} from '../api/meetingHistoryApi'
import {
  liveFullDetail,
  liveFullPreview,
  liveFullResponse,
  liveFullSource,
  liveMarker,
} from '../api/meetingLiveFull.fixtures'
import { liveFinal, liveLegRef } from '../api/meetingLiveTranscript.fixtures'
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
const detail = (changes: Record<string, unknown> = {}) =>
  normalizeHistoryDetail(liveFullDetail(changes))!
const renderDetail = (meeting = detail(), language = 'fr') => {
  const i18n = createInstance()
  void i18n.init({
    lng: language,
    initAsync: false,
    resources: { fr: { meetingHistory: fr }, en: { meetingHistory: en } },
  })
  const view = (next: MeetingHistoryDetail, available = true) => (
    <I18nextProvider i18n={i18n}>
      <MeetingTranscriptSection
        transcript={next.transcript}
        meetingRef={available ? next.id : undefined}
      />
    </I18nextProvider>
  )
  const result = render(view(meeting))
  return {
    ...result,
    refresh: (next: MeetingHistoryDetail, available = true) =>
      result.rerender(view(next, available)),
  }
}
const clickLoad = () =>
  fireEvent.click(
    screen.getByRole('button', { name: fr.transcript.live.full.load })
  )
const loaded = () =>
  screen.findByRole('region', { name: fr.transcript.live.full.title })

describe('live retained consultation through the real transcript section', () => {
  it.each([
    ['fr', fr],
    ['en', en],
  ] as const)(
    '%s reads over 1000 finals with corrected order, reconnections and exact copy beside the canonical body',
    async (language, messages) => {
      const events = Array.from({ length: 1001 }, (_, i) =>
        liveFinal({
          sequence: i + 1,
          item_ref: `item-${i}`,
          text: `  Passage ${i}.\n`,
        })
      )
      events[0] = liveFinal({
        sequence: 8000,
        item_ref: 'item-0',
        revision: 8,
        text: '  De\u0301cision : ne pas payer 250 €.\n',
      })
      const otherLeg = 'leg_reconnected0123456789'
      const reconnect = liveFinal({
        sequence: 8001,
        leg_ref: otherLeg,
        item_ref: 'item-0',
        text: '  Reconnexion.\n',
      })
      const raw = liveFullResponse({
        sources: [
          liveFullSource([
            ...events,
            liveMarker('gap'),
            liveMarker('end'),
            reconnect,
          ]),
        ],
      })
      vi.mocked(fetchApi).mockResolvedValue(raw)
      const writeText = vi.fn().mockResolvedValue(undefined)
      vi.stubGlobal('navigator', { clipboard: { writeText } })
      const meeting = detail()
      const before = JSON.stringify(meeting)
      const { container } = renderDetail(meeting, language)
      expect(fetchApi).not.toHaveBeenCalled()
      fireEvent.click(
        screen.getByRole('button', { name: messages.transcript.live.full.load })
      )
      const full = await screen.findByRole('region', {
        name: messages.transcript.live.full.title,
      })
      expect(within(full).getByText('Passage 1000.').textContent).toBe(
        events[1000].text
      )
      expect(full.querySelector('li > p')?.textContent).toBe(events[0].text)
      expect(full.querySelectorAll('[data-leg-ref]')).toHaveLength(2)
      fireEvent.click(
        within(full).getByRole('button', { name: messages.content.copy })
      )
      const expectedCopy = [
        messages.transcript.live.partial,
        'Source 1',
        messages.transcript.live.state.ended,
        messages.transcript.live.incomplete,
        messages.transcript.live.leg.replace('{{number}}', '1'),
        ...events.map((event) => event.text),
        messages.transcript.live.gap.capture_incomplete,
        messages.transcript.live.end.capture_incomplete,
        messages.transcript.live.leg.replace('{{number}}', '2'),
        reconnect.text,
      ].join('\n\n')
      expect(writeText).toHaveBeenCalledExactlyOnceWith(expectedCopy)
      expect(writeText.mock.calls[0][0]).not.toContain(liveLegRef)
      expect(container.querySelectorAll('time')).toHaveLength(0)
      expect(screen.getByText('Canonique prioritaire.')).toBeTruthy()
      expect(screen.getByText('00:05')).toBeTruthy()
      expect(JSON.stringify(meeting)).toBe(before)
      expect(fetchApi).toHaveBeenCalledTimes(1)
    }
  )

  const withdrawals = [
    ['absent', undefined],
    ['null', null],
    ['invalid', { ...liveFullPreview(), coverage: 'complete' }],
    ['no digest', { ...liveFullPreview(), source_heads_digest: undefined }],
    ['empty sources', { ...liveFullPreview(), sources: [] }],
    ['not truncated', { ...liveFullPreview(), truncated: false }],
    [
      'changed wire only',
      {
        ...liveFullPreview(),
        sources: [
          liveFullSource([liveFinal({ text: 'Aperçu frais différent.  ' })]),
        ],
      },
    ],
  ] as const
  it.each(
    withdrawals.flatMap(([name, raw]) =>
      (['loaded', 'in_flight'] as const).map((phase) => ({ name, raw, phase }))
    )
  )(
    'withdraws $phase text after a fresh $name body while HF stays cached',
    async ({ raw, phase }) => {
      let finish!: (value: unknown) => void
      if (phase === 'in_flight')
        vi.mocked(fetchApi).mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              finish = resolve
            })
        )
      else vi.mocked(fetchApi).mockResolvedValueOnce(liveFullResponse())
      const current = detail()
      const { refresh } = renderDetail(current)
      clickLoad()
      if (phase === 'loaded') await loaded()
      const signal = vi.mocked(fetchApi).mock.calls[0][1]?.signal
      const incoming = detail({
        live_transcript: raw,
        transcript_projection: {
          version: 1,
          state: 'transcribing',
          revision: 1,
          digest: null,
          source: null,
        },
      })
      refresh(mergeHistoryDetail(current, incoming))
      if (phase === 'in_flight') {
        expect(signal?.aborted).toBe(true)
        await act(async () => finish(liveFullResponse()))
      }
      expect(
        screen.queryByRole('region', { name: fr.transcript.live.full.title })
      ).toBeNull()
      expect(screen.getByText('Canonique prioritaire.')).toBeTruthy()
      expect(fetchApi).toHaveBeenCalledTimes(1)
    }
  )

  it.each(['loaded', 'in_flight'] as const)(
    'withdraws a %s read when history fails and requires a new click after recovery',
    async (phase) => {
      let finish!: (value: unknown) => void
      if (phase === 'in_flight')
        vi.mocked(fetchApi).mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              finish = resolve
            })
        )
      else vi.mocked(fetchApi).mockResolvedValueOnce(liveFullResponse())
      const current = detail()
      const { refresh } = renderDetail(current)
      clickLoad()
      if (phase === 'loaded') await loaded()
      const signal = vi.mocked(fetchApi).mock.calls[0][1]?.signal
      refresh(current, false)
      expect(
        screen.queryByRole('region', { name: fr.transcript.live.full.title })
      ).toBeNull()
      expect(
        screen.queryByRole('button', { name: fr.transcript.live.full.load })
      ).toBeNull()
      refresh(current)
      if (phase === 'in_flight') {
        expect(signal?.aborted).toBe(true)
        await act(async () => finish(liveFullResponse()))
      }
      expect(
        screen.queryByRole('region', { name: fr.transcript.live.full.title })
      ).toBeNull()
      vi.mocked(fetchApi).mockResolvedValueOnce(liveFullResponse())
      clickLoad()
      await loaded()
      expect(fetchApi).toHaveBeenCalledTimes(2)
    }
  )

  it.each(['loaded', 'in_flight'] as const)(
    'targets a new digest explicitly after a %s read is withdrawn',
    async (phase) => {
      let finish!: (value: unknown) => void
      if (phase === 'in_flight')
        vi.mocked(fetchApi).mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              finish = resolve
            })
        )
      else vi.mocked(fetchApi).mockResolvedValueOnce(liveFullResponse())
      const current = detail()
      const { refresh } = renderDetail(current)
      clickLoad()
      if (phase === 'loaded') await loaded()
      const signal = vi.mocked(fetchApi).mock.calls[0][1]?.signal
      refresh(
        mergeHistoryDetail(
          current,
          detail({
            live_transcript: liveFullPreview({
              source_heads_digest: 'c'.repeat(64),
            }),
          })
        )
      )
      if (phase === 'in_flight') {
        expect(signal?.aborted).toBe(true)
        await act(async () => finish(liveFullResponse()))
      }
      expect(
        screen.queryByRole('region', { name: fr.transcript.live.full.title })
      ).toBeNull()
      vi.mocked(fetchApi).mockResolvedValueOnce(
        liveFullResponse({ source_heads_digest: 'c'.repeat(64) })
      )
      clickLoad()
      await loaded()
      expect(vi.mocked(fetchApi).mock.calls[1][0]).toContain(
        `source_heads_digest=${'c'.repeat(64)}`
      )
    }
  )

  it('keeps a loaded consultation for an identical fresh wire body regardless of canonical revision', async () => {
    vi.mocked(fetchApi).mockResolvedValue(liveFullResponse())
    const current = detail()
    const { refresh } = renderDetail(current)
    clickLoad()
    await loaded()
    refresh(mergeHistoryDetail(current, detail()))
    expect(
      screen.getByRole('region', { name: fr.transcript.live.full.title })
    ).toBeTruthy()
    expect(fetchApi).toHaveBeenCalledTimes(1)
  })
  it.each([404, 409, 503])(
    'keeps a refusal %s opaque, without retrying automatically',
    async (status) => {
      vi.mocked(fetchApi).mockRejectedValue({ statusCode: status })
      renderDetail()
      clickLoad()
      expect(await screen.findByRole('alert')).toBeTruthy()
      expect(
        screen.queryByRole('region', { name: fr.transcript.live.full.title })
      ).toBeNull()
      expect(screen.getByText(fr.transcript.live.truncated)).toBeTruthy()
      expect(fetchApi).toHaveBeenCalledTimes(1)
    }
  )
  it('removes text and its copy action exactly at retention expiry', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-25T12:00:00Z'))
    vi.mocked(fetchApi).mockResolvedValue(
      liveFullResponse({ retention_expires_at: Date.now() / 1000 + 2 })
    )
    renderDetail()
    await act(async () => clickLoad())
    expect(
      screen.getByRole('region', { name: fr.transcript.live.full.title })
    ).toBeTruthy()
    act(() => vi.advanceTimersByTime(2000))
    expect(
      screen.queryByRole('region', { name: fr.transcript.live.full.title })
    ).toBeNull()
    expect(screen.getByText(fr.transcript.live.full.expired)).toBeTruthy()
    expect(
      screen.getAllByRole('button', { name: fr.content.copy })
    ).toHaveLength(1)
    expect(fetchApi).toHaveBeenCalledTimes(1)
  })
  it('aborts on unmount and cannot reuse loaded text on another meeting', async () => {
    let finish!: (value: unknown) => void
    vi.mocked(fetchApi).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    const first = renderDetail()
    clickLoad()
    const signal = vi.mocked(fetchApi).mock.calls[0][1]?.signal
    first.unmount()
    expect(signal?.aborted).toBe(true)
    renderDetail(
      detail({
        id: 'meeting_other0123456789',
        live_transcript: liveFullPreview({
          meeting_ref: 'meeting_other0123456789',
        }),
      })
    )
    await act(async () => finish(liveFullResponse()))
    expect(
      screen.queryByRole('region', { name: fr.transcript.live.full.title })
    ).toBeNull()
    expect(
      screen.getByRole('button', { name: fr.transcript.live.full.load })
    ).toBeTruthy()
    expect(fetchApi).toHaveBeenCalledTimes(1)
  })
  it('changes meeting during an outstanding read even when the source digest is identical', async () => {
    let finish!: (value: unknown) => void
    vi.mocked(fetchApi).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    const { refresh } = renderDetail()
    clickLoad()
    const signal = vi.mocked(fetchApi).mock.calls[0][1]?.signal
    refresh(
      detail({
        id: 'meeting_other0123456789',
        live_transcript: liveFullPreview({
          meeting_ref: 'meeting_other0123456789',
        }),
      })
    )
    expect(signal?.aborted).toBe(true)
    await act(async () => finish(liveFullResponse()))
    expect(
      screen.queryByRole('region', { name: fr.transcript.live.full.title })
    ).toBeNull()
  })
})
