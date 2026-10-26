import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
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
} from '../api/meetingLiveFull.fixtures'
import { liveDomainFixture } from '../api/meetingLiveObservedDomain.fixtures'
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
beforeEach(() => vi.mocked(fetchApi).mockReset())
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

const detail = (domain: unknown, changes: Record<string, unknown> = {}) =>
  normalizeHistoryDetail(
    liveFullDetail({
      live_transcript: liveFullPreview({ observed_domain: domain, ...changes }),
    })
  )!
function setup(meeting: MeetingHistoryDetail, language = 'fr') {
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
    screen.getByRole('button', { name: fr.transcript.live.full.load })
  )

describe('observed Live domain through the real history transcript section', () => {
  it.each([
    ['fr', fr],
    ['en', en],
  ] as const)(
    '%s shows an inventory with zero sources without claiming meeting coverage',
    (language, messages) => {
      const { container } = setup(
        detail(
          liveDomainFixture({
            counts: {
              ...liveDomainFixture().counts,
              admitted_sources: 0,
              journal_ended_sources: 0,
              send_stop_acknowledged_sources: 0,
            },
          }),
          { sources: [], truncated: false }
        ),
        language
      )
      const region = screen.getByRole('region', {
        name: messages.transcript.live.domain.title,
      })
      expect(region.textContent).toContain(
        messages.transcript.live.domain.unverified
      )
      expect(region.textContent).toContain(
        language === 'fr'
          ? 'Sources live admises : 0'
          : 'Admitted live sources: 0'
      )
      expect(container.textContent).not.toContain(
        liveDomainFixture().domain_ref
      )
      expect(container.textContent).not.toContain(
        liveDomainFixture().snapshot_digest
      )
      expect(screen.getByText('Canonique prioritaire.')).toBeTruthy()
      expect(fetchApi).not.toHaveBeenCalled()
    }
  )

  it('keeps unknown counts explicit while independent retained text remains readable', async () => {
    vi.mocked(fetchApi).mockResolvedValueOnce(liveFullResponse())
    setup(
      detail(
        liveDomainFixture({
          state: 'unknown',
          counts: null,
          blockers: [{ reason: 'inventory_contradiction', epoch_ref: null }],
        })
      )
    )
    expect(screen.getByText(fr.transcript.live.domain.unknown)).toBeTruthy()
    expect(
      screen.getByText(fr.transcript.live.domain.blockers.contradiction)
    ).toBeTruthy()
    clickLoad()
    expect(
      await screen.findByRole('region', { name: fr.transcript.live.full.title })
    ).toBeTruthy()
    expect(fetchApi).toHaveBeenCalledTimes(1)
  })

  const replacements = [
    [
      'projection-only change',
      liveDomainFixture({ projection_digest: '3'.repeat(64) }),
    ],
    ['absent', undefined],
    ['null', null],
    ['malformed', { counts: null }],
    [
      'invalidation',
      liveDomainFixture({
        state: 'unknown',
        counts: null,
        projection_digest: '4'.repeat(64),
        blockers: [{ reason: 'identity_conflict', epoch_ref: null }],
      }),
    ],
  ] as const
  it.each(
    replacements.flatMap(([name, raw]) =>
      ['loaded', 'pending'].map((phase) => ({ name, raw, phase }))
    )
  )(
    'withdraws $phase text on a fresh $name without changing the source-head digest',
    async ({ raw, phase }) => {
      let finish!: (value: unknown) => void
      if (phase === 'pending')
        vi.mocked(fetchApi).mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              finish = resolve
            })
        )
      else vi.mocked(fetchApi).mockResolvedValueOnce(liveFullResponse())
      const current = detail(liveDomainFixture())
      const { refresh } = setup(current)
      clickLoad()
      if (phase === 'loaded')
        await screen.findByRole('region', {
          name: fr.transcript.live.full.title,
        })
      const signal = vi.mocked(fetchApi).mock.calls[0][1]?.signal
      const incoming = detail(raw)
      expect(
        incoming.transcript.liveFullSnapshot?.sourceHeadsDigest ??
          'a'.repeat(64)
      ).toBe('a'.repeat(64))
      refresh(mergeHistoryDetail(current, incoming))
      if (phase === 'pending') {
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

  it.each(['pending', 'loaded'] as const)(
    'expires the observed inventory and %s reader at the original Q deadline',
    async (phase) => {
      vi.useFakeTimers()
      vi.setSystemTime(new Date('2026-10-25T12:00:00Z'))
      let finish!: (value: unknown) => void
      if (phase === 'pending')
        vi.mocked(fetchApi).mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              finish = resolve
            })
        )
      else vi.mocked(fetchApi).mockResolvedValueOnce(liveFullResponse())
      setup(
        detail(
          liveDomainFixture({ retention_expires_at: Date.now() / 1000 + 2 })
        )
      )
      clickLoad()
      if (phase === 'loaded')
        await act(async () => {
          await Promise.resolve()
        })
      const signal = vi.mocked(fetchApi).mock.calls[0][1]?.signal
      await act(async () => vi.advanceTimersByTime(2000))
      expect(
        screen.queryByRole('region', { name: fr.transcript.live.domain.title })
      ).toBeNull()
      expect(
        screen.queryByRole('region', { name: fr.transcript.live.full.title })
      ).toBeNull()
      expect(screen.getByText(fr.transcript.live.domain.expired)).toBeTruthy()
      if (phase === 'pending') {
        expect(signal?.aborted).toBe(true)
        await act(async () => finish(liveFullResponse()))
        expect(
          screen.queryByRole('region', { name: fr.transcript.live.full.title })
        ).toBeNull()
      }
      expect(screen.getByText('Canonique prioritaire.')).toBeTruthy()
      expect(fetchApi).toHaveBeenCalledTimes(1)
    }
  )

  it('does not render stale preview or inventory on an already expired response', () => {
    setup(detail(liveDomainFixture({ retention_expires_at: 1 })))
    expect(
      screen.queryByRole('region', { name: fr.transcript.live.domain.title })
    ).toBeNull()
    expect(
      screen.queryByRole('button', { name: fr.transcript.live.full.load })
    ).toBeNull()
    expect(screen.getByText(fr.transcript.live.domain.expired)).toBeTruthy()
    expect(fetchApi).not.toHaveBeenCalled()
  })
})
