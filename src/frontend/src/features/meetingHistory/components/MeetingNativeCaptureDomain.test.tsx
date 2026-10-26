import { cleanup, render, screen } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, describe, expect, it } from 'vitest'
import en from '@/locales/en/meetingHistory.json'
import fr from '@/locales/fr/meetingHistory.json'
import { normalizeNativeCoverage } from '../api/meetingNativeCoverage'
import { nativeDomainFixture } from '../api/meetingNativeCaptureDomain.fixtures'
import { fullCoverage, fullMeetingRef } from '../api/meetingNativeFull.fixtures'
import { MeetingNativeCoverage } from './MeetingNativeCoverage'

afterEach(cleanup)
const setup = (language: 'fr' | 'en', domain: unknown) => {
  const i18n = createInstance()
  void i18n.init({
    lng: language,
    initAsync: false,
    resources: { fr: { meetingHistory: fr }, en: { meetingHistory: en } },
  })
  const view = (value: unknown) => {
    const coverage = normalizeNativeCoverage(
      fullCoverage({ capture_domain: value }),
      fullMeetingRef
    )
    if (!coverage) throw new Error('Invalid synthetic domain fixture')
    return (
      <I18nextProvider i18n={i18n}>
        <MeetingNativeCoverage coverage={coverage} />
      </I18nextProvider>
    )
  }
  const result = render(view(domain))
  return {
    ...result,
    refresh: (value: unknown) => result.rerender(view(value)),
  }
}

describe('known capture inventory in the existing coverage section', () => {
  it.each([
    ['fr', fr],
    ['en', en],
  ] as const)(
    '%s retains incomplete coverage, failed captures and zero ASR results in a closed inventory',
    (language, messages) => {
      const raw = nativeDomainFixture({
        counts: {
          ...nativeDomainFixture().counts,
          verified_sources: 0,
          verified_results: 0,
        },
      })
      const { container } = setup(language, raw)
      expect(
        screen.getByText(messages.transcript.native.coverage.unverified)
      ).toBeTruthy()
      expect(
        screen.getByText(messages.transcript.native.domain.state.closed)
      ).toBeTruthy()
      expect(
        screen.getByRole('group', {
          name: messages.transcript.native.domain.title,
        }).textContent
      ).toContain(
        language === 'fr' ? 'résultats disponibles : 0' : 'available results: 0'
      )
      expect(
        screen.getByText(
          language === 'fr'
            ? 'Captures en échec : 1 ; interrompues : 0 ; arrêtées par limite : 0.'
            : 'Failed captures: 1; aborted: 0; stopped at limit: 0.'
        )
      ).toBeTruthy()
      expect(container.textContent).not.toContain(raw.domain_ref)
      expect(container.textContent).not.toContain(raw.snapshot_digest)
      expect(container.textContent).not.toContain('{{')
    }
  )
  it('withdraws former counts on unknown and removes the domain when the fresh authority omits it', () => {
    const { refresh } = setup('fr', nativeDomainFixture())
    expect(screen.getByText(/Pistes connues : 2/)).toBeTruthy()
    refresh(
      nativeDomainFixture({
        producer_revision: 4,
        snapshot_digest: 'f'.repeat(64),
        closure_state: 'unknown',
        reason: 'payload_limit_exceeded',
        counts: null,
      })
    )
    expect(screen.queryByText(/Pistes connues/)).toBeNull()
    expect(
      screen.queryByText(fr.transcript.native.domain.state.closed)
    ).toBeNull()
    expect(
      screen.getByText(
        fr.transcript.native.domain.reason.payload_limit_exceeded
      )
    ).toBeTruthy()
    refresh(null)
    expect(
      screen.queryByRole('group', { name: fr.transcript.native.domain.title })
    ).toBeNull()
    expect(
      screen.getByText(fr.transcript.native.coverage.unverified)
    ).toBeTruthy()
  })
  it('explains each known blocker once while waiting for final capture states', () => {
    setup(
      'fr',
      nativeDomainFixture({
        closure_state: 'closing',
        blockers: [
          {
            reason: 'capture_not_drained',
            epoch_ref: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            observation_ref: null,
          },
          {
            reason: 'capture_not_drained',
            epoch_ref: null,
            observation_ref: null,
          },
          {
            reason: 'start_unresolved',
            epoch_ref: null,
            observation_ref: null,
          },
        ],
      })
    )
    expect(
      screen.getByText(fr.transcript.native.domain.state.closing)
    ).toBeTruthy()
    expect(
      screen.getAllByText(
        fr.transcript.native.domain.blocker.capture_not_drained
      )
    ).toHaveLength(1)
    expect(
      screen.getByText(fr.transcript.native.domain.blocker.start_unresolved)
    ).toBeTruthy()
  })
})
