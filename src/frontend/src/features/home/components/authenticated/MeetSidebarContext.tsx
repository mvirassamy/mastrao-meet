import { VideoChatIcon } from '@/icons'
import { SettingsButton } from '@/features/settings'
import { css } from '@/styled-system/css'

type MeetSidebarContextProps = {
  collapsed?: boolean
  mobile?: boolean
}

export const MeetSidebarContext = ({
  collapsed = false,
  mobile = false,
}: MeetSidebarContextProps) => {
  if (collapsed) {
    return (
      <div className={css({ display: 'flex', justifyContent: 'center' })}>
        <SettingsButton
          dialogAppearance="app"
          buttonProps={{
            size: 'icon',
            className: css({
              width: '44px',
              height: '44px',
              minWidth: '44px',
              minHeight: '44px',
              // Boxed like the organization selector of the collapsed
              // Mastrao application sidebar.
              borderColor: 'border!',
              borderRadius: '12px',
              backgroundColor: 'card!',
              boxShadow: '0 1px 2px 0 rgb(0 0 0 / 0.05)',
              '&[data-hovered]': { backgroundColor: 'accent!' },
            }),
          }}
        />
      </div>
    )
  }

  return (
    <div
      className={css({
        display: 'flex',
        alignItems: 'center',
        minHeight: '44px',
        overflow: 'hidden',
        border: '1px solid',
        borderColor: 'border',
        borderRadius: '11px',
        backgroundColor: 'card',
      })}
    >
      <div
        aria-label="Mastrao Visio"
        className={css({
          display: 'flex',
          minWidth: 0,
          flex: 1,
          alignItems: 'center',
          gap: '9px',
          paddingX: '11px',
          color: 'foreground',
          fontSize: '0.8125rem',
          fontWeight: 400,
        })}
      >
        <VideoChatIcon
          size={19}
          aria-hidden="true"
          className={css({ flexShrink: 0, color: 'primary' })}
        />
        <span
          className={css({
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          })}
        >
          Mastrao Visio
        </span>
      </div>
      <span
        aria-hidden="true"
        className={css({
          width: '1px',
          height: '24px',
          backgroundColor: 'border',
        })}
      />
      <SettingsButton
        dialogAppearance="app"
        buttonProps={{
          size: 'icon',
          className: css({
            width: mobile ? '44px' : '40px',
            height: mobile ? '44px' : '40px',
            minWidth: mobile ? '44px' : '40px',
            minHeight: mobile ? '44px' : '40px',
            flexShrink: 0,
            borderRadius: 0,
          }),
        }}
      />
    </div>
  )
}
