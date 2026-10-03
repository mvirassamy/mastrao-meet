import { isMobileBrowser } from '@livekit/components-core'

export const useNoiseReductionAvailable = () => !isMobileBrowser()
