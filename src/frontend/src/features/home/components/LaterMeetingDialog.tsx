import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Bold, Button, Dialog, type DialogProps, P, Text } from '@/primitives'
import { CheckIcon, CopyIcon, LinkIcon, WarningIcon } from '@/icons'
import { Link } from 'wouter'
import { isMastraoRoomId } from '@/features/rooms/utils/isRoomValid'
import { css } from '@/styled-system/css'
import { ApiAccessLevel, ApiRoom } from '@/features/rooms/api/ApiRoom'
import { useTelephony } from '@/features/rooms/livekit/hooks/useTelephony'
import { formatPinCode } from '@/features/rooms/utils/telephony'
import { useCopyRoomToClipboard } from '@/features/rooms/livekit/hooks/useCopyRoomToClipboard'
import type { VideoInvitation } from '../api/createCanonicalMeeting'

// fixme - duplication with the InviteDialog
export const LaterMeetingDialog = ({
  room,
  invitations = [],
  ...dialogProps
}: { room: null | ApiRoom; invitations?: VideoInvitation[] } & Omit<
  DialogProps,
  'title' | 'children'
>) => {
  const { t } = useTranslation('home', { keyPrefix: 'laterMeetingDialog' })

  const telephony = useTelephony()

  const isTelephonyReadyForUse = useMemo(() => {
    return telephony?.enabled && room?.pin_code
  }, [telephony?.enabled, room?.pin_code])

  const {
    isCopied,
    copyRoomToClipboard,
    isRoomUrlCopied,
    copyRoomUrlToClipboard,
    shareUrl,
    shareUrlDisplay,
    isShareLinkPending,
    shareLinkError,
  } = useCopyRoomToClipboard(room || undefined)
  const isCopyDisabled =
    !shareUrl || isShareLinkPending || Boolean(shareLinkError)
  const unconfirmed = invitations.filter(
    (invitation) => invitation.delivery_state !== 'sent'
  )

  return (
    <Dialog
      isOpen={!!room}
      {...dialogProps}
      appearance="app"
      title={t('heading')}
    >
      <P>{t('description')}</P>
      {invitations.length > 0 && (
        <Text as="p" role="status">
          {unconfirmed.length
            ? t('invitationsUnconfirmed', {
                emails: unconfirmed
                  .map((invitation) => invitation.email)
                  .join(', '),
              })
            : t('invitationsSubmitted')}
        </Text>
      )}
      {room && isMastraoRoomId(room.slug) && (
        <Link href={`/host/${room.slug}`}>{t('recoverHost')}</Link>
      )}
      {!!room && (
        <>
          {isTelephonyReadyForUse ? (
            <div
              className={css({
                width: '100%',
                backgroundColor: 'muted',
                borderRadius: '0.75rem',
                display: 'flex',
                flexDirection: 'column',
                padding: '1.75rem 1.5rem',
                marginTop: '0.5rem',
                gap: '1rem',
                overflow: 'hidden',
              })}
            >
              <div
                className={css({
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                })}
              >
                <Text as="p" wrap="pretty">
                  {shareUrlDisplay}
                </Text>
                {isTelephonyReadyForUse && (
                  <Button
                    variant={isRoomUrlCopied ? 'secondary' : 'ghost'}
                    size="icon-sm"
                    onPress={copyRoomUrlToClipboard}
                    isDisabled={isCopyDisabled}
                    aria-label={t('copyUrl')}
                    tooltip={t('copyUrl')}
                  >
                    {isRoomUrlCopied ? (
                      <CheckIcon aria-hidden="true" />
                    ) : (
                      <CopyIcon aria-hidden="true" />
                    )}
                  </Button>
                )}
              </div>
              <div
                className={css({
                  display: 'flex',
                  flexDirection: 'column',
                })}
              >
                <Text as="p" wrap="pretty">
                  <Bold>{t('phone.call')}</Bold> ({telephony?.country}){' '}
                  {telephony?.internationalPhoneNumber}
                </Text>
                <Text as="p" wrap="pretty">
                  <Bold>{t('phone.pinCode')}</Bold>{' '}
                  {formatPinCode(room?.pin_code)}
                </Text>
              </div>
              <Button
                variant={isCopied ? 'secondary' : 'ghost'}
                size="sm"
                fullWidth
                aria-label={t('copy')}
                style={{
                  justifyContent: 'start',
                }}
                onPress={copyRoomToClipboard}
                isDisabled={isCopyDisabled}
                data-attr="later-dialog-copy"
              >
                {isCopied ? (
                  <>
                    <CheckIcon
                      size={18}
                      style={{ marginRight: '8px' }}
                      aria-hidden="true"
                    />
                    {t('copied')}
                  </>
                ) : (
                  <>
                    <CopyIcon
                      style={{ marginRight: '6px', minWidth: '18px' }}
                      aria-hidden="true"
                    />
                    {t('copy')}
                  </>
                )}
              </Button>
            </div>
          ) : (
            <div
              className={css({
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                marginTop: '0.75rem',
                padding: '0.25rem 0.25rem 0.25rem 0.75rem',
                border: '1px solid token(colors.border)',
                borderRadius: '8px',
                backgroundColor:
                  'color-mix(in srgb, var(--muted) 50%, transparent)',
              })}
            >
              <LinkIcon
                size={16}
                aria-hidden="true"
                className={css({ flexShrink: 0, color: 'muted-foreground' })}
              />
              <span
                className={css({
                  minWidth: 0,
                  flex: 1,
                  overflow: 'hidden',
                  color: 'foreground',
                  fontSize: '0.875rem',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                })}
              >
                {shareUrlDisplay}
              </span>
              <Button
                size="sm"
                variant={isCopied ? 'secondary' : 'default'}
                onPress={copyRoomToClipboard}
                isDisabled={isCopyDisabled}
                data-attr="later-dialog-copy"
                icon={
                  isCopied ? (
                    <CheckIcon aria-hidden="true" />
                  ) : (
                    <CopyIcon aria-hidden="true" />
                  )
                }
                className={css({
                  flexShrink: 0,
                  minHeight: { base: '40px', md: '32px' },
                  whiteSpace: 'nowrap',
                })}
              >
                {isCopied ? t('copied') : t('copy')}
              </Button>
            </div>
          )}
          {isShareLinkPending && (
            <Text variant="sm" role="status">
              {t('preparing')}
            </Text>
          )}
          {shareLinkError && (
            <Text variant="sm" role="alert">
              {t('unavailable')}
            </Text>
          )}
          {room?.access_level == ApiAccessLevel.PUBLIC && (
            <p
              className={css({
                display: 'flex',
                alignItems: 'flex-start',
                gap: '0.5rem',
                marginTop: '0.75rem',
                marginBottom: 0,
                color: 'muted-foreground',
                fontSize: '0.8125rem!',
                lineHeight: '1.25rem',
              })}
            >
              <WarningIcon
                size={16}
                aria-hidden="true"
                className={css({
                  flexShrink: 0,
                  marginTop: '0.125rem',
                  color: 'primary',
                })}
              />
              {t('permissions')}
            </p>
          )}
        </>
      )}
    </Dialog>
  )
}
