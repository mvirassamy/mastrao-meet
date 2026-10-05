import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MeetingHistoryDetailSkeleton } from './MeetingHistoryDetailSkeleton'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(cleanup)

describe('meeting detail skeleton', () => {
  it('announces only the loading label and draws both sections', () => {
    render(<MeetingHistoryDetailSkeleton label="Chargement de la réunion…" />)

    expect(screen.getByRole('status').textContent).toContain(
      'Chargement de la réunion…'
    )
    // The section titles are drawn but hidden from screen readers, and no
    // heading competes with the page title that replaces the skeleton.
    for (const title of ['summary.title', 'transcript.title'])
      expect(
        screen.getByText(title).closest('[aria-hidden="true"]')
      ).not.toBeNull()
    expect(screen.queryByRole('heading')).toBeNull()
  })
})
