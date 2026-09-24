import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConnectionState } from 'livekit-client'

import { LeaveButton } from './LeaveButton'

const disconnect = vi.fn()
const navigateTo = vi.fn()
const reportError = vi.fn()
let connectionState = ConnectionState.Connected

vi.mock('@livekit/components-react', () => ({
  useRoomContext: () => ({ disconnect }),
  useConnectionState: () => connectionState,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@/navigation/navigateTo', () => ({
  navigateTo: (...args: unknown[]) => navigateTo(...args),
}))
vi.mock('@/features/analytics/telemetry', () => ({
  reportError: (...args: unknown[]) => reportError(...args),
}))
vi.mock('@/primitives', () => ({
  Button: ({
    children,
    onPress,
    isDisabled,
  }: {
    children: ReactNode
    onPress: () => void
    isDisabled?: boolean
  }) => (
    <button disabled={isDisabled} onClick={onPress}>
      {children}Leave
    </button>
  ),
}))

describe('LeaveButton after connection loss', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    connectionState = ConnectionState.Connected
    disconnect.mockResolvedValue(undefined)
  })
  afterEach(cleanup)

  it('lets a disconnected participant leave even when the SDK emits no event', async () => {
    connectionState = ConnectionState.Disconnected
    render(<LeaveButton />)
    fireEvent.click(screen.getByRole('button', { name: 'Leave' }))
    await waitFor(() =>
      expect(navigateTo).toHaveBeenCalledWith('feedback', { outcome: 'left' })
    )
    expect(disconnect).toHaveBeenCalledWith(true)
  })

  it('keeps the existing SDK disconnection flow during an active call', () => {
    render(<LeaveButton />)
    fireEvent.click(screen.getByRole('button', { name: 'Leave' }))
    expect(disconnect).toHaveBeenCalledWith(true)
    expect(navigateTo).not.toHaveBeenCalled()
  })
})
