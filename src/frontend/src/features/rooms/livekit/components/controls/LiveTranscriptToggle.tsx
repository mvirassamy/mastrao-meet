import { useTranslation } from 'react-i18next'

import { isLiveTranscriptPanelEnabled, useConfig } from '@/api/useConfig'
import { TranscriptIcon } from '@/icons'
import { ToggleButton } from '@/primitives'
import type { ToggleButtonProps } from '@/primitives/ToggleButton'
import { css } from '@/styled-system/css'

import { useSidePanel } from '../../hooks/useSidePanel'
import { useLiveTranscription } from '@/features/subtitle/store'

const ACTIVE_TRANSCRIPTION_STATUSES = new Set([
  'starting',
  'live',
  'reconnecting',
  'degraded',
  'stopping',
])

export const LiveTranscriptToggle = ({
  onPress,
  ...props
}: Partial<ToggleButtonProps>) => {
  const { t } = useTranslation('rooms', {
    keyPrefix: 'controls.liveTranscript',
  })
  const { data } = useConfig()
  const { isLiveTranscriptOpen, toggleLiveTranscript } = useSidePanel()
  const { status } = useLiveTranscription()
  const tooltipLabel = isLiveTranscriptOpen ? 'open' : 'closed'
  const isTranscriptionActive = ACTIVE_TRANSCRIPTION_STATUSES.has(status)
  const label = t(tooltipLabel)
  const accessibleLabel = isTranscriptionActive
    ? `${label} — ${t('transcriptionInProgress')}`
    : label

  if (!isLiveTranscriptPanelEnabled(data)) return null

  return (
    <ToggleButton
      shape="circle"
      variant="ghost"
      aria-label={accessibleLabel}
      tooltip={accessibleLabel}
      isSelected={isLiveTranscriptOpen}
      aria-expanded={isLiveTranscriptOpen}
      onPress={(event) => {
        toggleLiveTranscript()
        onPress?.(event)
      }}
      data-attr={`controls-live-transcript-${tooltipLabel}`}
      data-transcription-status={status}
      {...props}
    >
      <TranscriptIcon />
      {isTranscriptionActive && (
        <span
          aria-hidden="true"
          data-testid="live-transcript-active-indicator"
          className={css({
            position: 'absolute',
            right: '0.125rem',
            top: '0.125rem',
            width: '0.5rem',
            height: '0.5rem',
            borderRadius: '50%',
            backgroundColor: 'success-foreground',
            boxShadow: '0 0 0 2px var(--colors-box-bg)',
          })}
        />
      )}
    </ToggleButton>
  )
}
