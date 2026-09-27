import { RiCloseFill } from '@remixicon/react'
import { Link } from 'wouter'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import { MastraoSidebarIcon } from './mastraoIcons'

type MeetSidebarBrandProps = {
  collapsed?: boolean
  mobile?: boolean
  onToggle: () => void
  toggleLabel: string
}

const MastraoLogo = () => (
  <svg
    viewBox="270 335 780 485"
    width="29"
    height="22"
    aria-hidden="true"
    focusable="false"
  >
    <image href="/assets/mastrao-logo-icon.png" width="1254" height="1254" />
  </svg>
)

export const MeetSidebarBrand = ({
  collapsed = false,
  mobile = false,
  onToggle,
  toggleLabel,
}: MeetSidebarBrandProps) => (
  <div
    className={css({
      display: 'flex',
      alignItems: 'center',
      justifyContent: collapsed ? 'center' : 'space-between',
      gap: '0.5rem',
      height: mobile ? '44px' : '36px',
      paddingX: collapsed ? 0 : '0.5rem',
    })}
  >
    {/* Collapsed rail: like the Mastrao application, only the toggle stays. */}
    {!collapsed && (
      <Link
        to="/"
        className={css({
          display: 'flex',
          minWidth: 0,
          minHeight: mobile ? '44px' : '29px',
          alignItems: 'center',
          gap: '0.625rem',
          color: 'foreground',
          textDecoration: 'none',
          _focusVisible: {
            outline: '2px solid',
            outlineColor: 'ring',
            outlineOffset: '3px',
            borderRadius: 'control',
          },
        })}
      >
        <span
          className={css({
            display: 'grid',
            width: '29px',
            height: '29px',
            flexShrink: 0,
            placeItems: 'center',
          })}
        >
          <MastraoLogo />
        </span>
        <span
          className={css({
            overflow: 'hidden',
            fontSize: '1.1875rem',
            fontWeight: 600,
            letterSpacing: '-0.015em',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          })}
        >
          Mastrao
        </span>
      </Link>
    )}
    <Button
      square
      size="appIcon"
      variant="secondaryText"
      aria-label={toggleLabel}
      tooltip={toggleLabel}
      onPress={onToggle}
      className={css({
        width: mobile ? '44px' : collapsed ? '40px' : '33px',
        height: mobile ? '44px' : collapsed ? '40px' : '33px',
        minWidth: mobile ? '44px' : collapsed ? '40px' : '33px',
        minHeight: mobile ? '44px' : collapsed ? '40px' : '33px',
        flexShrink: 0,
        borderRadius: mobile ? 'control' : collapsed ? '12px' : '9px',
        '& svg': { width: '19px', height: '19px' },
      })}
    >
      {mobile ? <RiCloseFill aria-hidden="true" /> : <MastraoSidebarIcon />}
    </Button>
  </div>
)
