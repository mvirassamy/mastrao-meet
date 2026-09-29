import { fetchApi } from '@/api/fetchApi'

export type ApiSubtitleLifecycleState =
  | 'inactive'
  | 'starting'
  | 'live'
  | 'reconnecting'
  | 'degraded'
  | 'unavailable'
  | 'stopping'
  | 'stopped'

export type ApiSubtitleStateResponse = {
  subtitle?: {
    state?: ApiSubtitleLifecycleState
    stateVersion?: number
    sessionId?: string | null
    updatedAt?: string | null
    reason?: string | null
    desired?: 'ON' | 'OFF'
    roomSid?: string | null
  }
}

export const fetchSubtitleState = (
  roomId: string,
  token: string,
  signal?: AbortSignal
) =>
  fetchApi<ApiSubtitleStateResponse>(`rooms/${roomId}/subtitle-state/`, {
    signal,
    headers: { Authorization: `Bearer ${token}` },
  })
