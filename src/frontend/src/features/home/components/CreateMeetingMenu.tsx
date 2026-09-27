import { useTranslation } from 'react-i18next'
import { MenuItem, Menu as RACMenu } from 'react-aria-components'
import { Button, Menu, type ButtonProps } from '@/primitives'
import { navigateTo } from '@/navigation/navigateTo'
import { RiAddFill, RiLinksFill, RiVideoAddFill } from '@remixicon/react'
import { LaterMeetingDialog } from '@/features/home/components/LaterMeetingDialog'
import { useState } from 'react'

import { menuRecipe } from '@/primitives/menuRecipe'
import { ApiAccessLevel, type ApiRoom } from '@/features/rooms/api/ApiRoom'
import { useIsMutating } from '@tanstack/react-query'
import { reportError } from '@/features/analytics/telemetry'
import { css } from '@/styled-system/css'
import {
  canonicalMeetingMutationKey,
  createIdempotencyKey,
  useCreateCanonicalMeeting,
} from '@/features/home/api/createCanonicalMeeting'

// The canonical room is identified by its reference, which is also its slug.
const laterRoomFromRef = (roomRef: string): ApiRoom => ({
  id: roomRef,
  name: roomRef,
  slug: roomRef,
  is_administrable: false,
  access_level: ApiAccessLevel.RESTRICTED,
})

type CreateMeetingMenuProps = {
  label?: string
  showIcon?: boolean
  buttonProps?: Omit<ButtonProps, 'children' | 'icon'>
}

export const CreateMeetingMenu = ({
  label,
  showIcon = false,
  buttonProps,
}: CreateMeetingMenuProps = {}) => {
  const { t } = useTranslation('home')
  const { mutateAsync: createMeetingRequest, isPending } =
    useCreateCanonicalMeeting()
  const activeCreateRequests = useIsMutating({
    mutationKey: canonicalMeetingMutationKey,
  })
  const isCreating = isPending || activeCreateRequests > 0
  const [laterRoom, setLaterRoom] = useState<null | ApiRoom>(null)
  const [creationFailed, setCreationFailed] = useState(false)

  const createMeeting = async (forLater: boolean) => {
    if (isCreating) return
    setCreationFailed(false)
    // One key per user action; automatic retries replay this exact key.
    const idempotencyKey = createIdempotencyKey()
    try {
      const { roomRef } = await createMeetingRequest(idempotencyKey)
      if (forLater) {
        setLaterRoom(laterRoomFromRef(roomRef))
        return
      }
      // The backend already attached the host grant to this session.
      navigateTo('room', roomRef)
    } catch (error) {
      setCreationFailed(true)
      reportError('generic_failure', error, {
        context: 'Failed to create meeting room:',
      })
    }
  }

  return (
    <>
      <div className={css({ display: 'flex', flexDirection: 'column' })}>
        <Menu density="app">
          <Button
            variant="primary"
            data-attr="create-meeting"
            icon={showIcon ? <RiVideoAddFill aria-hidden="true" /> : undefined}
            isDisabled={isCreating}
            loading={isCreating}
            {...buttonProps}
          >
            {label ?? t('createMeeting')}
          </Button>
          <RACMenu className={menuRecipe({ density: 'app' }).root}>
            <MenuItem
              className={
                menuRecipe({
                  density: 'app',
                  icon: true,
                  variant: 'light',
                }).item
              }
              isDisabled={isCreating}
              onAction={() => void createMeeting(false)}
              data-attr="create-option-instant"
            >
              <RiAddFill aria-hidden="true" />
              {t('createMenu.instantOption')}
            </MenuItem>
            <MenuItem
              className={
                menuRecipe({
                  density: 'app',
                  icon: true,
                  variant: 'light',
                }).item
              }
              isDisabled={isCreating}
              onAction={() => void createMeeting(true)}
              data-attr="create-option-later"
            >
              <RiLinksFill aria-hidden="true" />
              {t('createMenu.laterOption')}
            </MenuItem>
          </RACMenu>
        </Menu>
        {creationFailed && (
          <p
            role="alert"
            className={css({
              color: 'danger',
              fontSize: '0.75rem',
              marginTop: '0.375rem',
            })}
          >
            {t('createMenu.error')}
          </p>
        )}
      </div>
      <LaterMeetingDialog
        room={laterRoom}
        onOpenChange={() => setLaterRoom(null)}
      />
    </>
  )
}
