import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MeetingHistoryDetailSkeleton } from './MeetingHistoryDetailSkeleton'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(cleanup)

describe('meeting detail skeleton', () => {
  it('announces only the loading label and keeps the drawing hidden', () => {
    render(<MeetingHistoryDetailSkeleton label="Chargement de la réunion…" />)

    expect(screen.getByRole('status').textContent).toBe(
      'Chargement de la réunion…'
    )
    // No heading competes with the page title that replaces the skeleton.
    expect(screen.queryByRole('heading')).toBeNull()
  })
})
