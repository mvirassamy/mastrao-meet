import { fetchApi } from '@/api/fetchApi'

export type RoomLifecycle = { state: 'open' | 'ending' | 'ended' }

const LIFECYCLE_TIMEOUT_MS = 5_000

export const fetchRoomLifecycle = async (
  roomId: string,
  signal?: AbortSignal
): Promise<RoomLifecycle> => {
  const controller = new AbortController()
  const abort = () => controller.abort()
  const timeout = window.setTimeout(abort, LIFECYCLE_TIMEOUT_MS)
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) abort()
  try {
    return await fetchApi(`/rooms/${roomId}/lifecycle/`, {
      signal: controller.signal,
    })
  } finally {
    window.clearTimeout(timeout)
    signal?.removeEventListener('abort', abort)
  }
}
