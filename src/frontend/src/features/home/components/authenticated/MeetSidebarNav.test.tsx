import { cleanup, render, screen } from '@testing-library/react'
import { Router } from 'wouter'
import { memoryLocation } from 'wouter/memory-location'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MeetSidebarNav } from './MeetSidebarNav'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        'dashboard.sidebar.label': 'Navigation Visio',
        'dashboard.sidebar.meetings': 'Réunions',
        'dashboard.sidebar.history': 'Historique',
      })[key] ?? key,
  }),
}))

const renderAt = (path: string) =>
  render(
    <Router hook={memoryLocation({ path }).hook}>
      <MeetSidebarNav />
    </Router>
  )

afterEach(cleanup)

describe('MeetSidebarNav', () => {
  it('places Historique right after Réunions', () => {
    renderAt('/')
    const links = screen.getAllByRole('link')
    expect(links.map((link) => link.textContent)).toEqual([
      'Réunions',
      'Historique',
    ])
    expect(links[1].getAttribute('href')).toBe('/reunions/historique')
  })

  it('marks only the current page', () => {
    renderAt('/reunions/historique/meeting-1')
    expect(
      screen
        .getByRole('link', { name: 'Historique' })
        .getAttribute('aria-current')
    ).toBe('page')
    expect(
      screen
        .getByRole('link', { name: 'Réunions' })
        .getAttribute('aria-current')
    ).toBeNull()
  })
})
