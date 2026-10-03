import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { decideRecording, decideTranscription } from '../api/recordingConsent'
import { RecordingConsent } from './RecordingConsent'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'fr' },
  }),
}))

vi.mock('@/primitives', () => ({
  Button: ({
    children,
    onPress,
    isDisabled,
  }: {
    children: string
    onPress?: () => void
    isDisabled?: boolean
  }) => (
    <button type="button" disabled={isDisabled} onClick={onPress}>
      {children}
    </button>
  ),
  H: ({ children }: { children: string }) => <h1>{children}</h1>,
  Text: ({ children }: { children: string }) => <p>{children}</p>,
}))

vi.mock('@/primitives/Checkbox', () => ({
  Checkbox: ({ children }: { children: string }) => <label>{children}</label>,
}))

vi.mock('@/styled-system/jsx', () => ({
  HStack: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  VStack: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

vi.mock('../api/recordingConsent', () => ({
  decideRecording: vi.fn(),
  decideTranscription: vi.fn(),
}))

describe('RecordingConsent delayed transcription notice', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the transcription notice and accept button after recording is already accepted', () => {
    render(
      <RecordingConsent
        roomId="room_0123456789abcdef"
        retentionExpiresAt={2_000_000_000}
        transcriptionOffered
        recordingDecision="accepted"
        transcriptionDecision="absent"
        onDecided={async () => undefined}
      />
    )

    expect(screen.getByText('transcription.notice')).toBeTruthy()
    const accept = screen.getByRole('button', { name: 'transcription.accept' })
    expect((accept as HTMLButtonElement).disabled).toBe(false)
  })

  it('persists a recording refusal without a separate transcription decision', async () => {
    const onDecided = vi.fn(async () => undefined)

    render(
      <RecordingConsent
        roomId="room_0123456789abcdef"
        retentionExpiresAt={2_000_000_000}
        transcriptionOffered
        recordingDecision="absent"
        transcriptionDecision="absent"
        onDecided={onDecided}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'refuse' }))

    await waitFor(() => {
      expect(decideRecording).toHaveBeenCalledWith(
        'room_0123456789abcdef',
        'refused',
        expect.stringMatching(/^refusal_/)
      )
      expect(onDecided).toHaveBeenCalledOnce()
    })
    expect(decideTranscription).not.toHaveBeenCalled()
  })
})
