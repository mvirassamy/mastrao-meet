import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AssistantCitationDialog } from '@/features/meeting-assistant/AssistantCitationDialog'
import type { MeetingAssistantSegment } from '@/features/meeting-assistant/types'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(cleanup)
const segment: MeetingAssistantSegment = {
  citation: {
    citation_id: 'citation_0123456789abcdef',
    meeting_ref: 'meeting_0123456789abcdef',
    source_kind: 'reviewed_revision',
    source_ref: 'source_0123456789abcdef',
    source_version: 3,
    source_digest: 'a'.repeat(64),
    segment_id: 'segment-1',
    start_ms: 12000,
    end_ms: 18000,
    time_basis: 'source_relative_ms',
  },
  text: 'Passage autorisé et relu.',
  speaker: 'Marie',
  status: 'transcript',
}

describe('authorized citation display', () => {
  it('shows the exact resolved passage with its source status and time basis', () => {
    render(
      <AssistantCitationDialog
        state={{ phase: 'ready', segment }}
        onClose={vi.fn()}
      />
    )
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByText('Passage autorisé et relu.')).toBeTruthy()
    expect(screen.getByText(/source.status.transcript/)).toBeTruthy()
    expect(screen.getByText(/source.time.source_relative_ms/)).toBeTruthy()
    expect(screen.queryByRole('link')).toBeNull()
  })
  it('clears a formerly readable passage while authorization is checked again or fails', () => {
    const { rerender } = render(
      <AssistantCitationDialog
        state={{ phase: 'ready', segment }}
        onClose={vi.fn()}
      />
    )
    rerender(
      <AssistantCitationDialog state={{ phase: 'loading' }} onClose={vi.fn()} />
    )
    expect(screen.queryByText('Passage autorisé et relu.')).toBeNull()
    expect(screen.getByRole('status').textContent).toBe('source.loading')
    rerender(
      <AssistantCitationDialog state={{ phase: 'error' }} onClose={vi.fn()} />
    )
    expect(screen.queryByText('Passage autorisé et relu.')).toBeNull()
    expect(screen.getByRole('alert').textContent).toBe('source.error')
  })
})
