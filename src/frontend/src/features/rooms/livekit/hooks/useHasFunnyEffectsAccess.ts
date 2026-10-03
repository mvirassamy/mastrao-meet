import useKonami from '@/features/rooms/livekit/hooks/useKonami'
import { konamiStore } from '@/stores/konami'
import { useSnapshot } from 'valtio'

export const useHasFunnyEffectsAccess = () => {
  const konamiSnap = useSnapshot(konamiStore)

  useKonami(
    () =>
      (konamiStore.areFunnyEffectsEnabled = !konamiSnap.areFunnyEffectsEnabled)
  )

  return konamiSnap.areFunnyEffectsEnabled
}
