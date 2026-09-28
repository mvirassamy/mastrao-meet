import { useTranslation } from 'react-i18next'
import { useMemo } from 'react'
import { VStack } from '@/styled-system/jsx'
import { css } from '@/styled-system/css'
import { CheckIcon, CopyIcon } from '@/icons'
import { Bold, Button, Div, Text } from '@/primitives'
import { useRoomData } from '../hooks/useRoomData'
import { formatPinCode } from '../../utils/telephony'
import { useTelephony } from '../hooks/useTelephony'
import { useCopyRoomToClipboard } from '../hooks/useCopyRoomToClipboard'

export const Info = () => {
  const { t } = useTranslation('rooms', { keyPrefix: 'info' })

  const data = useRoomData()

  const telephony = useTelephony()

  const isTelephonyReadyForUse = useMemo(() => {
    return telephony?.enabled && data?.pin_code
  }, [telephony?.enabled, data?.pin_code])

  const {
    isCopied,
    copyRoomToClipboard,
    shareUrlDisplay,
    isShareLinkPending,
    shareLinkError,
  } = useCopyRoomToClipboard(data)

  return (
    <Div
      display="flex"
      overflowY="scroll"
      padding="0 1.25rem"
      flexGrow={1}
      flexDirection="column"
      alignItems="start"
    >
      <VStack alignItems="start">
        <Text
          as="h3"
          variant="inherits"
          className={css({
            display: 'flex',
            alignItems: 'center',
            fontSize: '0.875rem',
            lineHeight: '1.25rem',
            fontWeight: 600,
          })}
        >
          {t('roomInformation.title')}
        </Text>
        <div
          className={css({
            gap: '0.15rem',
            display: 'flex',
            flexDirection: 'column',
          })}
        >
          <Text as="p" variant="xsNote" wrap="pretty">
            {shareUrlDisplay}
          </Text>
          {isTelephonyReadyForUse && (
            <>
              <Text as="p" variant="xsNote" wrap="pretty">
                <Bold>{t('roomInformation.phone.call')}</Bold> (
                {telephony?.country}) {telephony?.internationalPhoneNumber}
              </Text>
              <Text as="p" variant="xsNote" wrap="pretty">
                <Bold>{t('roomInformation.phone.pinCode')}</Bold>{' '}
                {formatPinCode(data?.pin_code)}
              </Text>
            </>
          )}
        </div>
        <Button
          size="sm"
          variant={isCopied ? 'secondary' : 'ghost'}
          aria-label={t('roomInformation.button.ariaLabel')}
          onPress={copyRoomToClipboard}
          isDisabled={isShareLinkPending || Boolean(shareLinkError)}
          data-attr="copy-info-sidepannel"
          style={{
            marginLeft: '-8px',
          }}
        >
          {isCopied ? (
            <>
              <CheckIcon aria-hidden="true" />
              {t('roomInformation.button.copied')}
            </>
          ) : (
            <>
              <CopyIcon aria-hidden="true" />
              {t('roomInformation.button.copy')}
            </>
          )}
        </Button>
        {isShareLinkPending && (
          <Text as="p" variant="xsNote">
            {t('roomInformation.preparing')}
          </Text>
        )}
        {shareLinkError && (
          <Text as="p" variant="xsNote">
            {t('roomInformation.unavailable')}
          </Text>
        )}
      </VStack>
    </Div>
  )
}
