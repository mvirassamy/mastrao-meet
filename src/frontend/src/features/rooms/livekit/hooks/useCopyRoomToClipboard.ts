import { useTelephony } from './useTelephony'
import { useTranslation } from 'react-i18next'
import { useEffect, useMemo, useState } from 'react'
import { formatPinCode } from '@/features/rooms/utils/telephony'
import type { ApiRoom } from '@/features/rooms/api/ApiRoom'
import { reportError } from '@/features/analytics/telemetry'
import { useRoomShareLink } from './useRoomShareLink'

const COPY_SUCCESS_TIMEOUT = 3000

export const useCopyRoomToClipboard = (room: ApiRoom | undefined) => {
  const telephony = useTelephony()
  const { t } = useTranslation('global', { keyPrefix: 'clipboardContent' })

  const [isCopied, setIsCopied] = useState(false)
  const [isRoomUrlCopied, setIsRoomUrlCopied] = useState(false)

  useEffect(() => {
    if (isCopied) {
      const timeout = setTimeout(() => setIsCopied(false), COPY_SUCCESS_TIMEOUT)
      return () => clearTimeout(timeout)
    }
  }, [isCopied])

  useEffect(() => {
    if (isRoomUrlCopied) {
      const timeout = setTimeout(
        () => setIsRoomUrlCopied(false),
        COPY_SUCCESS_TIMEOUT
      )
      return () => clearTimeout(timeout)
    }
  }, [isRoomUrlCopied])

  const { shareUrl, isShareLinkPending, shareLinkError } = useRoomShareLink(
    room?.slug
  )
  const shareUrlDisplay = shareUrl
    .replace(/^https?:\/\//, '')
    .replace(/#.*$/, '')

  const hasTelephonyInfo = useMemo(() => {
    return telephony.enabled && room?.pin_code
  }, [telephony.enabled, room])

  const content = useMemo(() => {
    if (!shareUrl || !room) return ''
    if (!hasTelephonyInfo) return shareUrl

    return [
      t('url', { roomUrl: shareUrl }),
      t('numberAndPin', {
        phoneNumber: telephony?.internationalPhoneNumber,
        pinCode: formatPinCode(room.pin_code),
      }),
    ].join('\n')
  }, [shareUrl, hasTelephonyInfo, telephony, room, t])

  const copyRoomToClipboard = async () => {
    if (!content) return
    try {
      await navigator.clipboard.writeText(content)
      setIsCopied(true)
    } catch (error) {
      reportError('clipboard_failure', error, {
        context: 'copy_room_content',
      })
    }
  }

  const copyRoomUrlToClipboard = async () => {
    if (!shareUrl) return
    try {
      await navigator.clipboard.writeText(shareUrl)
      setIsRoomUrlCopied(true)
    } catch (error) {
      reportError('clipboard_failure', error, {
        context: 'copy_room_url',
      })
    }
  }

  return {
    isCopied,
    copyRoomToClipboard,
    isRoomUrlCopied,
    copyRoomUrlToClipboard,
    shareUrl,
    shareUrlDisplay,
    isShareLinkPending,
    shareLinkError,
  }
}
