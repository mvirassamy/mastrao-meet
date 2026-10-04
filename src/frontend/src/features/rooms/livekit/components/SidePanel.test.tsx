import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { layoutStore } from '@/stores/layout'
import { PanelId, SubPanelId } from '../hooks/useSidePanel'
import { SidePanel } from './SidePanel'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      if (key === 'messages') return 'Messages'
      if (key === 'transcription') return 'Transcription'
      return key
    },
  }),
}))
vi.mock('@/primitives', () => ({
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
}))
vi.mock('@/features/participants/components/ParticipantsList', () => ({
  ParticipantsList: () => <div>Participants panel</div>,
}))
vi.mock('@/features/chat/components/Chat', () => ({
  Chat: () => <div>Messages panel</div>,
}))
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
vi.mock('@/hooks/useRestoreFocus', () => ({ useRestoreFocus: vi.fn() }))

beforeEach(() => {
  layoutStore.activePanelId = null
  layoutStore.activeSubPanelId = null
})

afterEach(() => {
  cleanup()
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

    fireEvent.click(screen.getByRole('tab', { name: 'Messages' }))
    expect(layoutStore.activePanelId).toBe(PanelId.CHAT)
    expect(await screen.findByText('Messages panel')).toBeTruthy()
    expect(screen.queryByText('Live transcription panel')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: 'Transcription' }))
    expect(layoutStore.activeSubPanelId).toBe(SubPanelId.LIVE_TRANSCRIPT)
    expect(await screen.findByText('Live transcription panel')).toBeTruthy()
  })
})
