import { fetchApi } from '@/api/fetchApi'

export type ApiSubtitleLifecycleState =
  | 'unknown'
  | 'inactive'
  | 'starting'
  | 'live'
  | 'reconnecting'
  | 'degraded'
  | 'unavailable'
  | 'stopping'
  | 'stopped'

export type ApiSubtitleStateResponse = {
  subtitle?: { state?: ApiSubtitleLifecycleState }
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
