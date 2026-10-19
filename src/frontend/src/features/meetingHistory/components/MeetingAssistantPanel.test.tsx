import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MeetingAssistantPanel } from '@/features/meeting-assistant/MeetingAssistantPanel'
import {
  AssistantResult,
  type AssistantResultView,
} from '@/features/meeting-assistant/AssistantResult'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(cleanup)
const result: AssistantResultView = {
  id: 'turn-a',
  outcome: 'answer',
  text: 'Proposition originale',
  coverage: ['Source partielle'],
  citations: [{ id: 'citation-a', label: '00:12' }],
  editable: true,
}

describe('private meeting assistant presentation', () => {
  it('does not send a question or action until explicitly requested', () => {
    const onAsk = vi.fn()
    const onAction = vi.fn()
    render(
      <MeetingAssistantPanel
        turns={[]}
        loading={false}
        pending={false}
        error={false}
        canSend={true}
        onRetry={vi.fn()}
        onAsk={onAsk}
        onAction={onAction}
        onOpenCitation={vi.fn()}
        onClose={vi.fn()}
      />
    )
    expect(onAsk).not.toHaveBeenCalled()
    expect(onAction).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('question'), {
      target: { value: ' Quel budget ? ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'ask' }))
    expect(onAsk).toHaveBeenCalledWith('Quel budget ?')
    fireEvent.click(
      screen.getByRole('button', { name: 'actions.follow-up-email' })
    )
    expect(onAction).toHaveBeenCalledWith('follow-up-email')
  })
  it('copies the edited proposal and resolves a citation only on click', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    const open = vi.fn()
    render(<AssistantResult result={result} onOpenCitation={open} />)
    expect(open).not.toHaveBeenCalled()
    fireEvent.change(screen.getByRole('textbox', { name: 'result.edit' }), {
      target: { value: 'Mon brouillon relu' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'result.copy' }))
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith('Mon brouillon relu')
    )
    fireEvent.click(screen.getByRole('button', { name: '00:12' }))
    expect(open).toHaveBeenCalledWith('citation-a')
    expect(screen.getByText('Source partielle')).toBeTruthy()
  })
  it('distinguishes factual abstention from a technical failure and hides unavailable history', () => {
    render(
      <AssistantResult
        result={{ ...result, outcome: 'abstention', editable: false }}
        onOpenCitation={vi.fn()}
      />
    )
    expect(screen.getByRole('status').textContent).toBe('result.abstention')
    cleanup()
    render(
      <AssistantResult
        result={{ ...result, outcome: 'unavailable' }}
        onOpenCitation={vi.fn()}
      />
    )
    expect(screen.queryByText('Proposition originale')).toBeNull()
    expect(screen.queryByRole('button', { name: '00:12' })).toBeNull()
    expect(screen.getByRole('status').textContent).toBe('result.unavailable')
  })
  it('shows the completed proposal when the same pending turn finishes', () => {
    const { rerender } = render(
      <AssistantResult
        result={{ ...result, outcome: 'processing', text: '', editable: false }}
        onOpenCitation={vi.fn()}
      />
    )
    expect(screen.queryByRole('textbox')).toBeNull()
    rerender(<AssistantResult result={result} onOpenCitation={vi.fn()} />)
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(
      'Proposition originale'
    )
  })
  it('opens a citation from the selected turn when its id also occurs in an earlier turn', () => {
    const open = vi.fn()
    render(
      <MeetingAssistantPanel
        turns={['turn-a', 'turn-b'].map((id) => ({
          id,
          question: id,
          result: { ...result, id },
        }))}
        loading={false}
        pending={false}
        error={false}
        canSend={true}
        onRetry={vi.fn()}
        onAsk={vi.fn()}
        onAction={vi.fn()}
        onOpenCitation={open}
        onClose={vi.fn()}
      />
    )
    fireEvent.click(screen.getAllByRole('button', { name: '00:12' })[1])
    expect(open).toHaveBeenCalledWith('turn-b', 'citation-a')
  })
  it('preserves an edited proposal during a rights reread and removes it if access fails', () => {
    const props = {
      turns: [{ id: result.id, question: 'Synthèse courte', result }],
      loading: false,
      pending: false,
      error: false,
      canSend: true,
      onRetry: vi.fn(),
      onAsk: vi.fn(),
      onAction: vi.fn(),
      onClose: vi.fn(),
      onOpenCitation: vi.fn(),
    }
    const { rerender } = render(<MeetingAssistantPanel {...props} />)
    fireEvent.change(screen.getByRole('textbox', { name: 'result.edit' }), {
      target: { value: 'Mon texte relu' },
    })
    rerender(
      <MeetingAssistantPanel {...props} loading={true} canSend={false} />
    )
    expect(screen.queryByRole('textbox', { name: 'result.edit' })).toBeNull()
    rerender(<MeetingAssistantPanel {...props} />)
    expect(
      (
        screen.getByRole('textbox', {
          name: 'result.edit',
        }) as HTMLTextAreaElement
      ).value
    ).toBe('Mon texte relu')
    rerender(<MeetingAssistantPanel {...props} error={true} canSend={false} />)
    expect(screen.queryByDisplayValue('Mon texte relu')).toBeNull()
  })
})
