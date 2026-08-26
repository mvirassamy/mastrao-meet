import { fetchApi } from '@/api/fetchApi'

export type RoomLifecycle = { state: 'open' | 'ending' | 'ended' }

export const fetchRoomLifecycle = (roomId: string): Promise<RoomLifecycle> =>
  fetchApi(`/rooms/${roomId}/lifecycle/`)
