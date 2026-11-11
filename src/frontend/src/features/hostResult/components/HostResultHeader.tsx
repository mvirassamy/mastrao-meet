import { Link } from 'wouter'
import { LoginButton } from '@/components/LoginButton'
import { SettingsButton } from '@/features/settings'
import { Logo } from '@/layout/Header'
import { css } from '@/styled-system/css'

/**
 * The public Meet header for a visitor: logo, login and settings. It reads no
 * session, so the result page calls no signed-in route.
 */
export const HostResultHeader = () => (
  <header
    className={css({
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: '1rem',
      minHeight: '62px',
      paddingY: '0.5rem',
      paddingX: { base: '1rem', md: '1.5rem' },
      flexShrink: 0,
      backgroundColor: 'card',
      color: 'card-foreground',
      borderBottom: '1px solid token(colors.border)',
    })}
  >
    <Link
      to="/"
      className={css({
        display: 'flex',
        padding: '0.25rem',
        borderRadius: '4px',
        _hover: { backgroundColor: 'muted' },
      })}
    >
      <Logo />
    </Link>
    <div
      className={css({ display: 'flex', alignItems: 'center', gap: '0.25rem' })}
    >
      {/* The link credential is gone from the address, so return home. */}
      <LoginButton
        proConnectHint={false}
        returnTo={`${window.location.origin}/`}
      />
      <SettingsButton dialogAppearance="app" />
    </div>
  </header>
)
