import { useMutation, type UseMutationOptions } from '@tanstack/react-query'
import { fetchApi } from '@/api/fetchApi'
import type { ApiError } from '@/api/ApiError'
import type { ApiRoom } from '@/features/rooms/api/ApiRoom'

export interface StartSubtitleParams {
  id: string
  token: string
}

const pendingStartSubtitleRequests = new Map<string, Promise<ApiRoom>>()

export const startSubtitle = ({
  id,
  token,
}: StartSubtitleParams): Promise<ApiRoom> => {
  const pendingRequest = pendingStartSubtitleRequests.get(id)
  if (pendingRequest) return pendingRequest

  const request = fetchApi<ApiRoom>(`rooms/${id}/start-subtitle/`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
    },
  }).finally(() => {
    if (pendingStartSubtitleRequests.get(id) === request) {
      pendingStartSubtitleRequests.delete(id)
    }
  })

  pendingStartSubtitleRequests.set(id, request)
  return request
}

export function useStartSubtitle(
  options?: UseMutationOptions<ApiRoom, ApiError, StartSubtitleParams>
) {
  return useMutation<ApiRoom, ApiError, StartSubtitleParams>({
    mutationFn: startSubtitle,
    ...options,
  })
}
