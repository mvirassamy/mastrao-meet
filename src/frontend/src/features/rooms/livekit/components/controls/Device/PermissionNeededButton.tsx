import { Button } from '@/primitives'
import { ErrorIcon } from '@/icons'
import { openPermissionsDialog } from '@/stores/permissions'
import { css } from '@/styled-system/css'
import { useTranslation } from 'react-i18next'

type PermissionNeededButtonProps = {
  tooltip?: string
  onPress?: () => void
}

export const PermissionNeededButton = ({
  tooltip,
  onPress,
}: PermissionNeededButtonProps) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'permissionsButton' })
  const label = tooltip ?? t('tooltip')
  return (
    <div
      className={css({
        position: 'absolute',
        // The 22px badge sits 7px outside the control, like a notification dot.
        left: '-8px',
        top: '-8px',
        zIndex: 1,
      })}
    >
      <Button
        aria-label={tooltip ? label : t('ariaLabel')}
        tooltip={label}
        onPress={onPress ?? (() => openPermissionsDialog())}
        variant="permission"
        className={css({
          // 24px hit area around the 22px badge.
          width: '24px!',
          height: '24px!',
          display: 'grid!',
          placeItems: 'center',
        })}
      >
        {/* White disc behind the cut-out "!" + ring matching the room. */}
        <span
          aria-hidden="true"
          className={css({
            display: 'grid',
            placeItems: 'center',
            width: '22px',
            height: '22px',
            borderRadius: '100%',
            backgroundColor: '#ffffff',
            boxShadow: '0 0 0 2px var(--background)',
          })}
        >
          <ErrorIcon size={22} />
        </span>
      </Button>
    </div>
  )
}
