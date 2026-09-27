import { useToast } from 'react-aria'
import { useRef } from 'react'

import { type ToastProps } from './Toast'
import { HStack } from '@/styled-system/jsx'
import { Button, Div } from '@/primitives'
import { useTranslation } from 'react-i18next'
import { CloseIcon, HandRaisedIcon } from '@/icons'
import { useSidePanel } from '@/features/rooms/livekit/hooks/useSidePanel'
import { StyledToastContainer } from './StyledToastContainer'

export function ToastRaised({ state, ...props }: Readonly<ToastProps>) {
  const { t } = useTranslation('notifications')
  const ref = useRef(null)
  const { toastProps, contentProps, titleProps, closeButtonProps } = useToast(
    props,
    state,
    ref
  )
  const participant = props.toast.content.participant
  const { isParticipantsOpen, toggleParticipants } = useSidePanel()

  if (!participant) return

  return (
    <StyledToastContainer {...toastProps} ref={ref}>
      <HStack
        justify="center"
        alignItems="center"
        {...contentProps}
        padding={14}
        gap={0}
      >
        <HandRaisedIcon
          color="currentColor"
          style={{
            marginRight: '1rem',
            animationDuration: '300ms',
            animationName: 'wave_hand',
            animationIterationCount: '2',
          }}
        />
        <Div {...titleProps} marginRight={0.5}>
          {t('raised.description', {
            name: participant.name || t('defaultName'),
          })}
        </Div>
        {!isParticipantsOpen && (
          <Button
            size="sm"
            variant="link"
            onPress={(e) => {
              toggleParticipants()
              closeButtonProps.onPress?.(e)
            }}
          >
            {t('raised.cta')}
          </Button>
        )}
        <Button size="icon-sm" variant="ghost" {...closeButtonProps}>
          <CloseIcon size={18} color="currentColor" />
        </Button>
      </HStack>
    </StyledToastContainer>
  )
}
