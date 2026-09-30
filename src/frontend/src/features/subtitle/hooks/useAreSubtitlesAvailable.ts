import { useConfig } from '@/api/useConfig'

export const useAreSubtitlesAvailable = () => {
  const { data } = useConfig()

  return data?.subtitle.enabled ?? false
}
