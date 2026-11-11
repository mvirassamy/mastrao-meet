import { ApiError } from '@/api/ApiError'

export type HostResultSpeaker =
  | null
  | { kind: 'participant'; label: string }
  | { kind: 'anonymous'; index: number }

export type HostResultSegment = {
  segment_id: string
  start_ms: number
  end_ms: number
  speaker: HostResultSpeaker
  text: string
}

export type HostResultSummaryItem = {
  kind: 'decision' | 'action' | 'deadline'
  statement: string
}

export type HostResult = {
  meeting_ref: string
  title: string
  access_expires_at: number
  /** Recorded period of the meeting audio, in epoch seconds. */
  started_at: number
  ended_at: number
  summary: {
    editorial_state: 'machine_generated' | 'published'
    summary: { text: string }
    items: HostResultSummaryItem[]
  }
  transcript: {
    language: string | null
    segments: HostResultSegment[]
  }
}

const readUrl = () => {
  const origin =
    import.meta.env.VITE_API_BASE_URL ||
    (typeof window !== 'undefined' ? window.location.origin : '')
  return `${origin.replace(/\/$/, '')}/handoff/host/result/`
}

export const readHostResult = async (access: string): Promise<HostResult> => {
  const response = await fetch(readUrl(), {
    method: 'POST',
    credentials: 'omit',
    referrerPolicy: 'no-referrer',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ access }),
  })
  const result = await response.json()
  if (!response.ok) throw new ApiError(response.status, result)
  return result as HostResult
}
