import type { SpeakerPresentation } from './liveTranscriptSpeakerCache'
import { createContext, useContext } from 'react'
import type {
  LiveTranscriptionAction,
  LiveTranscriptionState,
} from './liveTranscriptionTypes'

export type LiveTranscriptionContextValue = LiveTranscriptionState & {
  resolveSpeaker: (identity: string) => SpeakerPresentation | undefined
  dispatch: (action: LiveTranscriptionAction) => void
  syncSubtitleState: () => Promise<void>
}

export const LiveTranscriptionContext =
  createContext<LiveTranscriptionContextValue | null>(null)

export const useLiveTranscription = () => {
  const context = useContext(LiveTranscriptionContext)
  if (!context) {
    throw new Error(
      'useLiveTranscription must be used inside LiveTranscriptionProvider'
    )
  }
  return context
}
