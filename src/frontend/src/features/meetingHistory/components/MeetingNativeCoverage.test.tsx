import { cleanup, render, screen } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, describe, expect, it } from 'vitest'
import en from '@/locales/en/meetingHistory.json'
import fr from '@/locales/fr/meetingHistory.json'
import type { MeetingNativeCoverage as NativeCoverage } from '../api/types'
import { MeetingNativeCoverage } from './MeetingNativeCoverage'
import { nativeDomainFixture } from '../api/meetingNativeCaptureDomain.fixtures'
import { normalizeNativeCaptureDomain } from '../api/meetingNativeCaptureDomain'

afterEach(cleanup)

const coverage = (overrides: Partial<NativeCoverage> = {}): NativeCoverage => ({
  meetingCoverage: 'unverified',
  observationsAvailable: true,
  truncated: false,
  tracks: [],
  epochs: [],
  ...overrides,
})

const renderCoverage = async (
  value: NativeCoverage,
  locale: 'fr' | 'en' = 'fr'
) => {
  const i18n = createInstance()
  await i18n.init({
    lng: locale,
    resources: { fr: { meetingHistory: fr }, en: { meetingHistory: en } },
    initAsync: false,
  })
  return render(
    <I18nextProvider i18n={i18n}>
      <MeetingNativeCoverage coverage={value} />
    </I18nextProvider>
  )
}

describe('native source coverage', () => {
  it.each(['fr', 'en'] as const)(
    'keeps coverage unverified when every known result is available in %s',
    async (locale) => {
      const text = { fr, en }[locale].transcript.native.coverage
      await renderCoverage(
        coverage({
          tracks: [
            {
              observationRef: 'private-observation-ref',
              captureState: 'admitted',
              epochRefs: ['private-epoch-ref'],
            },
          ],
          epochs: [{ epochRef: 'private-epoch-ref', state: 'available' }],
        }),
        locale
      )

      const section = screen.getByRole('region', { name: text.title })
      expect(screen.getByText(text.unverified)).toBeTruthy()
      expect(section.querySelectorAll('p')).toHaveLength(1)
      expect(section.textContent).not.toContain('private-')
      expect(screen.queryByRole('button')).toBeNull()
      expect(screen.queryByRole('link')).toBeNull()
    }
  )

  it('reports pending, failed, unadmitted and missing results without technical identifiers', async () => {
    await renderCoverage(
      coverage({
        truncated: true,
        tracks: [
          {
            observationRef: 'pending-source',
            captureState: 'admitted',
            epochRefs: ['pending-epoch'],
          },
          {
            observationRef: 'failed-source',
            captureState: 'admitted',
            epochRefs: ['failed-epoch'],
          },
          {
            observationRef: 'unadmitted-source',
            captureState: 'unadmitted',
            epochRefs: [],
          },
        ],
        epochs: [
          { epochRef: 'pending-epoch', state: 'pending' },
          { epochRef: 'failed-epoch', state: 'failed' },
        ],
      })
    )

    const text = fr.transcript.native.coverage
    for (const key of [
      'unverified',
      'truncated',
      'pending',
      'failed',
      'unadmitted',
      'missingResults',
    ] as const)
      expect(screen.getByText(text[key])).toBeTruthy()
    expect(screen.queryByText(text.unknownLinks)).toBeNull()
    expect(document.body.textContent).not.toMatch(
      /pending-source|failed-source|unadmitted-source|pending-epoch|failed-epoch/
    )
  })

  it('distinguishes unspecified source links from an explicit empty list', async () => {
    const value = coverage({
      tracks: [{ observationRef: 'older-source', captureState: 'admitted' }],
    })
    const view = await renderCoverage(value)
    expect(
      screen.getByText(fr.transcript.native.coverage.unknownLinks)
    ).toBeTruthy()
    expect(
      screen.queryByText(fr.transcript.native.coverage.missingResults)
    ).toBeNull()
    view.unmount()

    await renderCoverage(
      coverage({
        tracks: [
          {
            observationRef: 'empty-source',
            captureState: 'admitted',
            epochRefs: [],
          },
        ],
      })
    )
    expect(
      screen.queryByText(fr.transcript.native.coverage.unknownLinks)
    ).toBeNull()
    expect(
      screen.getByText(fr.transcript.native.coverage.missingResults)
    ).toBeTruthy()
  })

  it('does not present unavailable observations as a confirmed absence of capture', async () => {
    await renderCoverage(coverage({ observationsAvailable: false }))
    expect(
      screen.getByText(fr.transcript.native.coverage.observationsUnavailable)
    ).toBeTruthy()
    expect(
      screen.getByText(fr.transcript.native.coverage.unverified)
    ).toBeTruthy()
    expect(
      screen.queryByText(fr.transcript.native.coverage.missingResults)
    ).toBeNull()
  })

  it('still reports known pending results when source observations are unavailable', async () => {
    await renderCoverage(
      coverage({
        observationsAvailable: false,
        epochs: [{ epochRef: 'legacy-pending-result', state: 'pending' }],
      })
    )
    expect(
      screen.getByText(fr.transcript.native.coverage.observationsUnavailable)
    ).toBeTruthy()
    expect(screen.getByText(fr.transcript.native.coverage.pending)).toBeTruthy()
  })
})

it.each(['fr', 'en'] as const)(
  'explains both consultation limits and preserves unknown domain metadata in %s',
  async (locale) => {
    const text = { fr, en }[locale].transcript.native
    for (const reason of [
      'epoch_limit_exceeded',
      'result_bytes_limit_exceeded',
    ] as const) {
      const domain = normalizeNativeCaptureDomain(
        nativeDomainFixture({
          closure_state: 'unknown',
          reason: 'epoch_limit_exceeded',
          counts: null,
        })
      )
      if (!domain) throw new Error('Expected invalidated domain')
      const view = await renderCoverage(
        coverage({ epochsUnavailableReason: reason, captureDomain: domain }),
        locale
      )
      expect(screen.getByText(text.coverage[reason])).toBeTruthy()
      expect(screen.getByText(text.domain.state.unknown)).toBeTruthy()
      expect(
        screen.getByText(text.domain.reason.epoch_limit_exceeded)
      ).toBeTruthy()
      expect(document.body.textContent).not.toContain('dddddddd')
      expect(screen.queryByRole('button')).toBeNull()
      view.unmount()
    }
  }
)
