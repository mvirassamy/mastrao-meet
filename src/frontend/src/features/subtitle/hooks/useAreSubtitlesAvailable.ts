import { useConfig } from '@/api/useConfig'

export const useAreSubtitlesAvailable = () => {
  const { data } = useConfig()

  return Boolean(data?.subtitle.enabled)
}
