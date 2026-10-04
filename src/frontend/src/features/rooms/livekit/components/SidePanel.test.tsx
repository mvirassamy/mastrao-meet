import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { layoutStore } from '@/stores/layout'
import { PanelId, SubPanelId } from '../hooks/useSidePanel'
import { SidePanel } from './SidePanel'
import { ChatToggle } from './controls/ChatToggle'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      if (key === 'messages') return 'Messages'
      if (key === 'transcription') return 'Transcription'
      return key
    },
  }),
}))
vi.mock('@/primitives', async () => ({
  TextArea: (await import('@/primitives/TextArea')).TextArea,
  ToggleButton: (await import('@/primitives/ToggleButton')).ToggleButton,
  Button: ({
    children,
    onPress,
    ...props
  }: {
    children: ReactNode
    onPress: () => void
    'aria-label'?: string
  }) => (
    <button type="button" onClick={onPress} aria-label={props['aria-label']}>
      {children}
    </button>
  ),
}))
vi.mock('@/primitives/appAppearance', () => ({
  AppAppearanceProvider: ({ children }: { children: ReactNode }) => children,
}))
vi.mock('@/icons', () => ({
  ArrowLeftIcon: () => null,
  CloseIcon: () => null,
  SendIcon: () => null,
  ChatIcon: () => null,
}))
vi.mock('@/features/participants/components/ParticipantsList', () => ({
  ParticipantsList: () => <div>Participants panel</div>,
}))
vi.mock('@/features/chat/components/Chat', async () => {
  const { ChatTextArea } =
    await import('@/features/chat/components/ChatTextArea')
  return {
    Chat: () => (
      <div>
        Messages panel
        <ChatTextArea />
      </div>
    ),
  }
})
vi.mock('@/features/subtitle/component/LiveTranscriptSidePanel', () => ({
  LiveTranscriptSidePanel: () => <div>Live transcription panel</div>,
}))
vi.mock('./effects/Effects', () => ({ Effects: () => null }))
vi.mock('./Admin', () => ({ Admin: () => null }))
vi.mock('./Tools', () => ({ Tools: () => null }))
vi.mock('./Info', () => ({ Info: () => null }))
vi.mock('@/features/reactions/hooks/useReactionsToolbar', () => ({
  useReactionsToolbar: () => ({ isOpen: false }),
}))
vi.mock('@/features/shortcuts/useRegisterKeyboardShortcut', () => ({
  useRegisterKeyboardShortcut: vi.fn(),
}))

const animationFrames = new Map<number, FrameRequestCallback>()
let nextFrameId = 0

const flushAnimationFrames = async () => {
  await act(async () => {
    const callbacks = [...animationFrames.values()]
    animationFrames.clear()
    callbacks.forEach((callback) => callback(performance.now()))
  })
}

beforeEach(() => {
  layoutStore.activePanelId = null
  layoutStore.activeSubPanelId = null
  vi.stubGlobal('CSS', { escape: (value: string) => value })
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    const id = ++nextFrameId
    animationFrames.set(id, callback)
    return id
  })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => {
    animationFrames.delete(id)
  })
})

afterEach(() => {
  cleanup()
  animationFrames.clear()
  vi.unstubAllGlobals()
})

describe('meeting conversation panel', () => {
  it('opens on the live transcription tab and switches to messages and back', async () => {
    render(<SidePanel />)

    expect(layoutStore.activePanelId).toBe(PanelId.TOOLS)
    expect(layoutStore.activeSubPanelId).toBe(SubPanelId.LIVE_TRANSCRIPT)
    expect(
      (await screen.findByRole('tab', { name: 'Transcription' })).getAttribute(
        'aria-selected'
      )
    ).toBe('true')
    expect(screen.getByText('Live transcription panel')).toBeTruthy()
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Transcription',
      'Messages',
    ])

    fireEvent.click(screen.getByRole('tab', { name: 'Messages' }))
    expect(layoutStore.activePanelId).toBe(PanelId.CHAT)
    expect(await screen.findByText('Messages panel')).toBeTruthy()
    expect(screen.queryByText('Live transcription panel')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: 'Transcription' }))
    expect(layoutStore.activeSubPanelId).toBe(SubPanelId.LIVE_TRANSCRIPT)
    expect(await screen.findByText('Live transcription panel')).toBeTruthy()
  })

  it('keeps focus on the selected tab while navigating with arrow keys', async () => {
    render(<SidePanel />)
    const transcription = await screen.findByRole('tab', {
      name: 'Transcription',
    })
    await flushAnimationFrames()
    act(() => transcription.focus())

    fireEvent.keyDown(transcription, { key: 'ArrowRight' })
    await screen.findByRole('textbox')
    await flushAnimationFrames()
    const messages = screen.getByRole('tab', { name: 'Messages' })
    expect(messages.getAttribute('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(messages)

    fireEvent.keyDown(document.activeElement!, { key: 'ArrowLeft' })
    await screen.findByText('Live transcription panel')
    await flushAnimationFrames()
    expect(transcription.getAttribute('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(transcription)
  })

  it('returns focus to the chat command after switching tabs and closing', async () => {
    render(
      <>
        <ChatToggle />
        <SidePanel />
      </>
    )
    await screen.findByRole('tab', { name: 'Transcription' })
    await flushAnimationFrames()
    fireEvent.click(screen.getByRole('button', { name: 'closeButton' }))
    await waitFor(() => expect(screen.queryByRole('tab')).toBeNull())

    const chatCommand = screen.getByRole('button', { name: 'closed' })
    act(() => chatCommand.focus())
    fireEvent.click(chatCommand)
    const input = await screen.findByRole('textbox')
    await flushAnimationFrames()
    expect(document.activeElement).toBe(input)

    const messages = screen.getByRole('tab', { name: 'Messages' })
    act(() => messages.focus())
    fireEvent.keyDown(messages, { key: 'ArrowLeft' })
    await screen.findByText('Live transcription panel')
    await flushAnimationFrames()
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowRight' })
    await screen.findByRole('textbox')
    await flushAnimationFrames()
    expect(document.activeElement).toBe(messages)

    const closeButton = screen.getByRole('button', { name: 'closeButton' })
    act(() => closeButton.focus())
    fireEvent.click(closeButton)
    await waitFor(() => expect(screen.queryByRole('tab')).toBeNull())
    await flushAnimationFrames()
    expect(document.activeElement).toBe(chatCommand)
  })
})
