import { useEffect, useRef, useState } from 'react'
import { openAssistantCitation } from './assistantApi'
import type { CitationState } from './AssistantCitationDialog'
import type { MeetingAssistantCitation } from './types'

/** Every opening resolves the descriptor again under the current source rights. */
export function useAssistantCitation(revision: number) {
  const [state, setState] = useState<CitationState>({ phase: 'closed' })
  const request = useRef<AbortController | null>(null)
  useEffect(() => {
    request.current?.abort()
    setState({ phase: 'closed' })
    return () => {
      request.current?.abort()
    }
  }, [revision])

  async function open(citation: MeetingAssistantCitation) {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setState({ phase: 'loading' })
    try {
      const segment = await openAssistantCitation(citation, controller.signal)
      if (!controller.signal.aborted) setState({ phase: 'ready', segment })
    } catch {
      if (!controller.signal.aborted) setState({ phase: 'error' })
    }
  }
  return {
    state,
    open: (citation: MeetingAssistantCitation) => void open(citation),
    close: () => {
      request.current?.abort()
      setState({ phase: 'closed' })
    },
  }
}
