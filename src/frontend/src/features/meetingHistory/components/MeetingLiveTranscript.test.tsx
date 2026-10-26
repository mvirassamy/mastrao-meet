import { cleanup, render, screen, within } from '@testing-library/react'
import { createInstance } from 'i18next'
import type { ReactNode } from 'react'
import { I18nextProvider } from 'react-i18next'
import { afterEach, describe, expect, it, vi } from 'vitest'
import en from '@/locales/en/meetingHistory.json'
import fr from '@/locales/fr/meetingHistory.json'
import de from '@/locales/de/meetingHistory.json'
import nl from '@/locales/nl/meetingHistory.json'
import { normalizeHistoryDetail } from '../api/meetingHistoryApi'
import {
  liveDetail,
  liveFinal,
  liveLegRef,
  liveSnapshot,
  liveSource,
  liveSourceRef,
} from '../api/meetingLiveTranscript.fixtures'
import { TranscriptBody } from './MeetingResultSections'
import { MeetingTranscriptSection } from './MeetingTranscriptSection'

vi.mock('@/icons', () => ({
  GroupIcon: () => null,
  InformationIcon: () => null,
  RetryIcon: () => null,
  CheckIcon: () => null,
  CopyIcon: () => null,
}))
vi.mock('@/primitives', () => ({
  Button: ({ children }: { children: ReactNode }) => (
    <button>{children}</button>
  ),
}))

afterEach(cleanup)

const renderLive = (
  changes: Record<string, unknown> = {},
  language = 'fr',
  section = false
) => {
  const i18n = createInstance()
  void i18n.init({
    lng: language,
    fallbackLng: 'en',
    resources: {
      fr: { meetingHistory: fr },
      en: { meetingHistory: en },
      de: { meetingHistory: de },
      nl: { meetingHistory: nl },
    },
    initAsync: false,
  })
  const detail = normalizeHistoryDetail(liveDetail(changes))
  if (!detail) throw new Error('Expected a meeting detail')
  return render(
    <I18nextProvider i18n={i18n}>
      {section ? (
        <MeetingTranscriptSection transcript={detail.transcript} />
      ) : (
        <TranscriptBody transcript={detail.transcript} />
      )}
    </I18nextProvider>
  )
}

describe('retained live transcript rendering', () => {
  it.each([
    ['gap', 'connection_lost'],
    ['gap', 'provider_failed'],
    ['gap', 'delivery_failed'],
    ['gap', 'timing_unavailable'],
    ['gap', 'capture_incomplete'],
    ['end', 'track_unpublished'],
    ['end', 'meeting_ended'],
    ['end', 'provider_failed'],
    ['end', 'capture_incomplete'],
  ])(
    'keeps the durable %s reason %s explicit even without speech',
    (kind, reason) => {
      const marker = {
        version: 2,
        source_ref: liveSourceRef,
        leg_ref: liveLegRef,
        sequence: 1,
        kind,
        reason,
        ...(kind === 'end' ? { final_event_count: 0 } : {}),
      }
      const { container } = renderLive({
        live_transcript: liveSnapshot([liveSource([marker])]),
      })
      const messages = fr.transcript.live[kind as 'gap' | 'end']
      expect(container.querySelector('li > p')?.textContent).toBe(
        messages[reason as keyof typeof messages]
      )
      expect(screen.getByText(fr.transcript.live.partial)).toBeTruthy()
    }
  )

  it('renders exact corrected text, partial scope and loss markers without invented times', () => {
    const corrected = liveFinal({ sequence: 8, revision: 3 })
    const nextItem = liveFinal({
      sequence: 2,
      item_ref: 'item-2',
      text: 'Ne pas confirmer.  ',
    })
    const gap = {
      version: 2,
      kind: 'gap',
      source_ref: liveSourceRef,
      leg_ref: liveLegRef,
      sequence: 9,
      reason: 'connection_lost',
    }
    const end = {
      ...gap,
      kind: 'end',
      sequence: 10,
      reason: 'capture_incomplete',
      final_event_count: 3,
    }
    const { container } = renderLive({
      live_transcript: {
        ...liveSnapshot([liveSource([corrected, nextItem, gap, end])]),
        truncated: true,
      },
    })
    const retained = screen.getByRole('region', {
      name: fr.transcript.live.title,
    })
    const paragraphs = Array.from(retained.querySelectorAll('li > p'))
    expect(paragraphs.map((paragraph) => paragraph.textContent)).toEqual([
      corrected.text,
      nextItem.text,
      fr.transcript.live.gap.connection_lost,
      fr.transcript.live.end.capture_incomplete,
    ])
    expect(within(retained).getByText(fr.transcript.live.partial)).toBeTruthy()
    expect(
      within(retained).getByText(fr.transcript.live.incomplete)
    ).toBeTruthy()
    expect(
      within(retained).getByText(fr.transcript.live.truncated)
    ).toBeTruthy()
    expect(screen.queryByText(fr.transcript.failed.title)).toBeNull()
    expect(container.textContent).not.toMatch(/\d{2}:\d{2}|120000|144000|24000/)
    expect(container.querySelector('time')).toBeNull()
  })

  it('separates repeated item IDs by source and capture leg', () => {
    const otherSource = 'source_other0123456789'
    const otherLeg = 'leg_other0123456789'
    const { container } = renderLive({
      live_transcript: liveSnapshot([
        liveSource([
          liveFinal({ text: 'A1' }),
          liveFinal({ leg_ref: otherLeg, sequence: 4, text: 'A2' }),
        ]),
        liveSource([liveFinal({ source_ref: otherSource, text: 'B1' })], {
          source_ref: otherSource,
          participant_kind: 'guest',
          incomplete: false,
        }),
      ]),
    })
    const sources = Array.from(container.querySelectorAll('[data-source-ref]'))
    expect(sources).toHaveLength(2)
    expect(
      sources.map((source) => source.getAttribute('data-source-ref'))
    ).toEqual([liveSourceRef, otherSource])
    expect(
      Array.from(sources[0].querySelectorAll('[data-leg-ref]')).map(
        (leg) => leg.querySelector('li')?.textContent
      )
    ).toEqual(['A1', 'A2'])
    expect(sources[1].querySelector('li')?.textContent).toBe('B1')
    expect(screen.getByText('Source 1 — Hôte')).toBeTruthy()
    expect(screen.getByText('Source 2 — Invité')).toBeTruthy()
    expect(screen.getAllByText(fr.transcript.live.incomplete)).toHaveLength(1)
  })

  it('shows retained live content during suspension without changing canonical status', () => {
    renderLive(
      {
        transcript_projection: {
          version: 1,
          state: 'transcribing',
          revision: 1,
          digest: null,
          source: null,
          processing_state: 'temporarily_suspended',
        },
      },
      'fr',
      true
    )
    expect(screen.getByRole('status').textContent).toBe(
      fr.transcript.temporarilySuspended.title
    )
    expect(
      screen.getByRole('region', { name: fr.transcript.live.title })
    ).toBeTruthy()
  })

  it('leaves canonical rendering and timestamps intact alongside a separate live section', () => {
    renderLive({
      transcript: {
        status: 'available',
        segments: [
          {
            id: 's1',
            start_ms: 5000,
            speaker: 'Camille',
            text: 'Canonical text.',
          },
        ],
      },
    })
    expect(screen.getByText('Canonical text.')).toBeTruthy()
    expect(screen.getByText('00:05')).toBeTruthy()
    const retained = screen.getByRole('region', {
      name: fr.transcript.live.title,
    })
    expect(retained.textContent).not.toMatch(/\d{2}:\d{2}/)
  })

  it.each(['fr', 'en', 'de', 'nl'])(
    'uses localized FR/EN copy with existing English fallback for %s',
    (language) => {
      renderLive({}, language)
      const strings = language === 'fr' ? fr : en
      expect(
        screen.getByRole('region', { name: strings.transcript.live.title })
      ).toBeTruthy()
      expect(screen.getByText(strings.transcript.live.partial)).toBeTruthy()
    }
  )

  it.each([undefined, null])(
    'keeps the existing unavailable path without live content: %j',
    (live) => {
      renderLive({ live_transcript: live })
      expect(screen.getByText(fr.transcript.failed.title)).toBeTruthy()
      expect(
        screen.queryByRole('region', { name: fr.transcript.live.title })
      ).toBeNull()
    }
  )

  it('does not render text from an invalid final', () => {
    renderLive({
      live_transcript: liveSnapshot([
        liveSource([
          liveFinal({ text: 'Invalid must stay hidden.', revision: 0 }),
        ]),
      ]),
    })
    expect(screen.queryByText('Invalid must stay hidden.')).toBeNull()
    expect(screen.getByText(fr.transcript.failed.title)).toBeTruthy()
  })
})
