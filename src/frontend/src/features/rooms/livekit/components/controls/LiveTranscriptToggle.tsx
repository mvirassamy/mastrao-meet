import { useTranslation } from 'react-i18next'

import { isLiveTranscriptPanelEnabled, useConfig } from '@/api/useConfig'
import { TranscriptIcon } from '@/icons'
import { ToggleButton } from '@/primitives'
import type { ToggleButtonProps } from '@/primitives/ToggleButton'

import { useSidePanel } from '../../hooks/useSidePanel'

export const LiveTranscriptToggle = ({
  onPress,
  ...props
}: Partial<ToggleButtonProps>) => {
  const { t } = useTranslation('rooms', {
    keyPrefix: 'controls.liveTranscript',
  })
  const { data } = useConfig()
  const { isLiveTranscriptOpen, toggleLiveTranscript } = useSidePanel()
  const tooltipLabel = isLiveTranscriptOpen ? 'open' : 'closed'

  if (!isLiveTranscriptPanelEnabled(data)) return null

  return (
    <ToggleButton
      shape="circle"
      variant="ghost"
      aria-label={t(tooltipLabel)}
      tooltip={t(tooltipLabel)}
      isSelected={isLiveTranscriptOpen}
      aria-expanded={isLiveTranscriptOpen}
      onPress={(event) => {
        toggleLiveTranscript()
        onPress?.(event)
      }}
      data-attr={`controls-live-transcript-${tooltipLabel}`}
      {...props}
    >
      <TranscriptIcon />
    </ToggleButton>
  )
}
