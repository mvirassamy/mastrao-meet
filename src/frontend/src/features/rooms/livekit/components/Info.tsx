import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import {
  CheckIcon,
  CopyIcon,
  LinkIcon,
  ShieldCheckIcon,
  WarningIcon,
} from '@/icons'
import { Button, Text } from '@/primitives'
import { ApiAccessLevel } from '../../api/ApiRoom'
import { useRoomData } from '../hooks/useRoomData'
import { useCopyRoomToClipboard } from '../hooks/useCopyRoomToClipboard'

const linkField = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.625rem',
  width: '100%',
  minHeight: '2.5rem',
  padding: '0.3125rem 0.3125rem 0.3125rem 0.75rem',
  borderRadius: '8px',
  border: '1px solid',
  borderColor: 'border',
  backgroundColor: 'muted',
})

const linkText = css({
  flex: 1,
  minWidth: 0,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  fontSize: '0.875rem',
})

const hiddenPart = css({ color: 'muted-foreground', letterSpacing: '0.05em' })

const sectionLabel = css({
  marginBottom: '0.75rem',
  fontSize: '0.75rem',
  fontWeight: 600,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  color: 'muted-foreground',
})

const iconTile = css({
  display: 'grid',
  placeItems: 'center',
  flexShrink: 0,
  width: '2rem',
  height: '2rem',
  borderRadius: '8px',
  backgroundColor: 'accent',
  color: 'primary',
})

export const Info = () => {
  const { t } = useTranslation('rooms', { keyPrefix: 'info.roomInformation' })
  const room = useRoomData()
  const {
    shareUrl,
    shareUrlDisplay,
    isShareLinkPending,
    shareLinkError,
    isRoomUrlCopied,
    copyRoomUrlToClipboard,
  } = useCopyRoomToClipboard(room)
  // A guest link carries its access key after "#": keep it off screen.
  const isLinkPartlyHidden = shareUrl.includes('#')
  const copyLabel = isRoomUrlCopied ? t('copied') : t('copy')

  const linkHint = () => {
    if (isShareLinkPending) return t('preparing')
    if (shareLinkError) return t('unavailable')
    if (isRoomUrlCopied) return t('copied')
    if (isLinkPartlyHidden) return t('hiddenPart')
    return null
  }

  return (
    <div
      className={css({
        display: 'flex',
        flexDirection: 'column',
        flexGrow: 1,
        overflowY: 'auto',
        padding: '0 1.25rem 1.25rem',
      })}
    >
      <Text as="p" variant="note" className={css({ marginBottom: '1rem' })}>
        {t('description')}
      </Text>
      <div className={linkField}>
        <LinkIcon
          size={18}
          aria-hidden="true"
          className={css({ color: 'muted-foreground' })}
        />
        <span className={linkText}>
          {shareUrlDisplay}
          {isLinkPartlyHidden && (
            <span aria-hidden="true" className={hiddenPart}>
              /••••••
            </span>
          )}
        </span>
        <Button
          variant={isRoomUrlCopied ? 'secondary' : 'outline'}
          size="icon-sm"
          onPress={copyRoomUrlToClipboard}
          isDisabled={!shareUrl}
          aria-label={copyLabel}
          tooltip={copyLabel}
          data-attr="copy-info-sidepannel"
        >
          {isRoomUrlCopied ? (
            <CheckIcon aria-hidden="true" />
          ) : (
            <CopyIcon aria-hidden="true" />
          )}
        </Button>
      </div>
      <Text
        as="p"
        variant="xsNote"
        aria-live="polite"
        className={css({ marginTop: '0.5rem', minHeight: '1rem' })}
      >
        {linkHint()}
      </Text>
      <AccessSummary
        isOpenAccess={room?.access_level === ApiAccessLevel.PUBLIC}
      />
    </div>
  )
}

const AccessSummary = ({ isOpenAccess }: { isOpenAccess: boolean }) => {
  const { t } = useTranslation('rooms', {
    keyPrefix: 'info.roomInformation.access',
  })
  const access = isOpenAccess ? 'open' : 'approval'
  const AccessIcon = isOpenAccess ? WarningIcon : ShieldCheckIcon

  return (
    <section className={css({ marginTop: '1.5rem' })}>
      <h3 className={sectionLabel}>{t('label')}</h3>
      <div className={css({ display: 'flex', gap: '0.75rem' })}>
        <span className={iconTile}>
          <AccessIcon size={16} aria-hidden="true" />
        </span>
        <div>
          <Text as="p" variant="sm" className={css({ fontWeight: 600 })}>
            {t(`${access}.title`)}
          </Text>
          <Text as="p" variant="xsNote">
            {t(`${access}.description`)}
          </Text>
        </div>
      </div>
    </section>
  )
}
