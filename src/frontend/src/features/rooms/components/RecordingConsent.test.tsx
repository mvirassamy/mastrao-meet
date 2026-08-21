import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
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
  HStack: ({ children }: { children: unknown }) => <div>{children}</div>,
  VStack: ({ children }: { children: unknown }) => <div>{children}</div>,
}))

vi.mock('../api/recordingConsent', () => ({
  decideRecording: vi.fn(),
  decideTranscription: vi.fn(),
}))

describe('RecordingConsent delayed transcription notice', () => {
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
})
