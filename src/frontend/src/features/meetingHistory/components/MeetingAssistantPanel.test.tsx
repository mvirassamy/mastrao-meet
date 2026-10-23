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
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})
const result: AssistantResultView = {
  id: 'turn-a',
  outcome: 'answer',
  answer: { kind: 'text', text: 'Réponse originale' },
  citations: [{ id: 'citation-a', label: '00:12' }],
  sourceLimit: null,
}
const panelProps = {
  readiness: 'ready' as const,
  turns: [],
  loading: false,
  opening: false,
  pending: false,
  error: false,
  canSend: true,
  canClear: true,
  onClear: vi.fn(),
  onRetry: vi.fn(),
  onAsk: vi.fn(),
  onAction: vi.fn(),
  onOpenCitation: vi.fn(),
  onClose: vi.fn(),
}

describe('private meeting assistant presentation', () => {
  it('lets the question be written while the history loads', () => {
    const onAsk = vi.fn()
    render(
      <MeetingAssistantPanel
        {...panelProps}
        loading
        opening
        canSend={false}
        onAsk={onAsk}
      />
    )
    expect(screen.getByRole('status').textContent).toBe('loading')
    const field = screen.getByLabelText<HTMLTextAreaElement>('question')
    expect(field.readOnly).toBe(false)
    fireEvent.change(field, { target: { value: 'Quelle date ?' } })
    expect(field.value).toBe('Quelle date ?')
    expect(
      screen.getByRole<HTMLButtonElement>('button', { name: 'ask' }).disabled
    ).toBe(true)
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(onAsk).not.toHaveBeenCalled()
  })

  it('does not send a question or action until explicitly requested', async () => {
    const onAsk = vi.fn()
    const onAction = vi.fn()
    render(
      <MeetingAssistantPanel
        {...panelProps}
        onAsk={onAsk}
        onAction={onAction}
      />
    )
    expect(onAsk).not.toHaveBeenCalled()
    expect(onAction).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('question'), {
      target: { value: ' Quel budget ? ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'ask' }))
    expect(onAsk).toHaveBeenCalledWith('Quel budget ?')
    // jsdom has no CSS.escape, which the open menu uses to focus its items.
    vi.stubGlobal('CSS', { escape: (value: string) => value })
    fireEvent.click(screen.getByRole('button', { name: 'actions.title' }))
    fireEvent.click(
      await screen.findByRole('menuitem', { name: /^actions\.follow-up-email/ })
    )
    expect(onAction).toHaveBeenCalledWith('follow-up-email')
  })
  it('waits for the transcript with the assistant greyed out', () => {
    render(<MeetingAssistantPanel {...panelProps} readiness="preparing" />)
    expect(screen.getByText('preparing.title')).toBeTruthy()
    expect(screen.getByText('preparing.transcribing')).toBeTruthy()
    expect(screen.getByPlaceholderText('placeholder.preparing')).toBeTruthy()
    expect(
      (screen.getByLabelText('question') as HTMLTextAreaElement).disabled
    ).toBe(true)
    expect(
      (
        screen.getByRole('button', {
          name: 'actions.title',
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    expect(screen.queryByRole('button', { name: 'options' })).toBeNull()
    cleanup()
    render(<MeetingAssistantPanel {...panelProps} readiness="unavailable" />)
    expect(screen.getByText('unavailable.title')).toBeTruthy()
  })
  it('writes out only the answer that just arrived', () => {
    const { rerender } = render(
      <AssistantResult result={result} arrived onOpenCitation={vi.fn()} />
    )
    // Each word is its own element, fading in one after the other.
    expect(screen.getByText('originale').tagName).toBe('SPAN')
    rerender(<AssistantResult result={result} onOpenCitation={vi.fn()} />)
    expect(screen.getByText('Réponse originale').tagName).toBe('P')
  })
  it('opens a citation only on click and adds no warning to a full answer', () => {
    const open = vi.fn()
    render(<AssistantResult result={result} onOpenCitation={open} />)
    expect(open).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'result.openSource' }))
    expect(open).toHaveBeenCalledWith('citation-a')
    expect(screen.getByText('Réponse originale')).toBeTruthy()
    expect(screen.queryByRole('textbox')).toBeNull()
  })
  it('shows a follow-up mail as a card ready to copy or send', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    render(
      <AssistantResult
        result={{
          ...result,
          answer: {
            kind: 'email',
            subject: 'Suivi',
            body: 'Bonjour,\n\nMerci.',
          },
          sourceLimit: 'Une partie de la réunion n’a pas été transcrite.',
        }}
        onOpenCitation={vi.fn()}
      />
    )
    expect(screen.getByRole('region', { name: 'email.label' })).toBeTruthy()
    expect(
      screen.getByRole('link', { name: 'email.open' }).getAttribute('href')
    ).toBe('mailto:?subject=Suivi&body=Bonjour%2C%0A%0AMerci.')
    fireEvent.click(screen.getByRole('button', { name: 'email.copy' }))
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith('Suivi\n\nBonjour,\n\nMerci.')
    )
    expect(
      screen.getByText('Une partie de la réunion n’a pas été transcrite.')
    ).toBeTruthy()
  })
  it('distinguishes factual abstention from a technical failure and hides unavailable history', () => {
    render(
      <AssistantResult
        result={{ id: 'turn-a', outcome: 'abstention' }}
        onOpenCitation={vi.fn()}
      />
    )
    expect(screen.getByRole('status').textContent).toBe('result.abstention')
    cleanup()
    render(
      <AssistantResult
        result={{ id: 'turn-a', outcome: 'unavailable' }}
        onOpenCitation={vi.fn()}
      />
    )
    expect(screen.queryByText('Réponse originale')).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'result.openSource' })
    ).toBeNull()
    expect(screen.getByRole('status').textContent).toBe('result.unavailable')
  })
  it('opens a citation from the selected turn when its id also occurs in an earlier turn', () => {
    const open = vi.fn()
    render(
      <MeetingAssistantPanel
        {...panelProps}
        turns={['turn-a', 'turn-b'].map((id) => ({
          id,
          question: id,
          result: { ...result, id },
        }))}
        onOpenCitation={open}
      />
    )
    fireEvent.click(
      screen.getAllByRole('button', { name: 'result.openSource' })[1]
    )
    expect(open).toHaveBeenCalledWith('turn-b', 'citation-a')
  })
  it('removes displayed answers when access fails on reread', () => {
    const props = {
      ...panelProps,
      turns: [{ id: result.id, question: 'Synthèse courte', result }],
    }
    const { rerender } = render(<MeetingAssistantPanel {...props} />)
    expect(screen.getByText('Réponse originale')).toBeTruthy()
    rerender(<MeetingAssistantPanel {...props} error={true} canSend={false} />)
    expect(screen.queryByText('Réponse originale')).toBeNull()
  })
})
