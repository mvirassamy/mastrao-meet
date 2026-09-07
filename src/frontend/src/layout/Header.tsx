import { Link } from 'wouter'
import { css } from '@/styled-system/css'
import { HStack, Stack } from '@/styled-system/jsx'
import { useTranslation } from 'react-i18next'
import { Button, Text } from '@/primitives'
import { SettingsButton } from '@/features/settings'
import { useUser } from '@/features/auth/api/useUser'
import { useMatchesRoute } from '@/navigation/useMatchesRoute'
import { FeedbackBanner } from '@/components/FeedbackBanner'
import { Menu } from '@/primitives/Menu'
import { MenuList } from '@/primitives/MenuList'
import { LoginButton } from '@/components/LoginButton'
import { VisualOnlyTooltip } from '@/primitives/VisualOnlyTooltip'

import { useLoginHint } from '@/hooks/useLoginHint'
import { logout } from '@/features/auth/utils/logout'
import { useMemo } from 'react'

const Logo = () => (
  <span
    className={`Header-logo ${css({ display: 'inline-flex', alignItems: 'center', gap: '0.625rem', whiteSpace: 'nowrap', fontFamily: 'sans' })}`}
  >
    {/* Frame the supplied icon without its large surrounding margins. */}
    <svg
      viewBox="270 335 780 485"
      width="36"
      height="24"
      aria-hidden="true"
      focusable="false"
      className={css({ flexShrink: 0 })}
    >
      <image href="/assets/mastrao-logo-icon.png" width="1254" height="1254" />
    </svg>
    <span
      className={css({
        fontSize: '24px',
        fontWeight: 'normal',
        letterSpacing: '-0.75px',
      })}
    >
      Mastrao
    </span>
    <span
      className={css({
        fontSize: '14px',
        color: 'muted-foreground',
        borderLeft: '1px solid',
        borderColor: 'border',
        paddingLeft: '0.625rem',
      })}
    >
      Visio
    </span>
  </span>
)

const LoginHint = () => {
  const { t } = useTranslation()
  const { isVisible, closeLoginHint } = useLoginHint()
  if (!isVisible) return null
  return (
    <div
      className={css({
        position: 'absolute',
        top: '64px',
        right: '110px',
        zIndex: '100',
        outline: 'none',
        padding: '1.25rem',
        maxWidth: '350px',
        boxShadow: '0 2px 5px var(--shadow-color)',
        borderRadius: '1rem',
        backgroundColor: 'accent',
        display: 'none',
        xsm: {
          display: 'block',
        },
        sm: {
          top: '64px',
          right: '100px',
          zIndex: '100',
        },
        _after: {
          content: '""',
          position: 'absolute',
          top: '-10px',
          right: '20%',
          marginLeft: '-10px',
          borderWidth: '0 10px 10px 10px',
          borderStyle: 'solid',
          borderColor: 'transparent transparent var(--accent) transparent',
        },
      })}
    >
      <Text variant="h3" margin={false} bold>
        {t('loginHint.title')}
      </Text>
      <Text variant="paragraph" margin={false}>
        {t('loginHint.body')}
      </Text>
      <Button
        aria-label={t('loginHint.button.ariaLabel')}
        size="sm"
        className={css({
          marginLeft: 'auto',
        })}
        onPress={() => closeLoginHint()}
      >
        {t('loginHint.button.label')}
      </Button>
    </div>
  )
}

const HIDE_LOGIN_PARAM = 'hideLogin'

const isLoginButtonHidden = () => {
  if (typeof window === 'undefined') return false
  const value = new URLSearchParams(window.location.search).get(
    HIDE_LOGIN_PARAM
  )
  return value === 'true'
}

export const Header = () => {
  const { t } = useTranslation()
  const isHome = useMatchesRoute('home')
  const isLegalTerms = useMatchesRoute('legalTerms')
  const isAccessibility = useMatchesRoute('accessibility')
  const isTermsOfService = useMatchesRoute('termsOfService')
  const isRoom = useMatchesRoute('room')
  const { user, isLoggedIn } = useUser()

  const loginButtonDisabledByUrl = useMemo(() => isLoginButtonHidden(), [])

  const userLabel = user?.full_name || user?.email
  const loggedInTooltip = t('loggedInUserTooltip')
  const loggedInAriaLabel = userLabel
    ? `${loggedInTooltip} ${userLabel}`
    : loggedInTooltip

  return (
    <>
      <FeedbackBanner />
      <div
        className={css({
          backgroundColor: 'card',
          color: 'card-foreground',
          paddingY: '0.5rem',
          paddingX: '1rem',
          flexShrink: 0,
        })}
      >
        <HStack gap={0} justify="space-between" alignItems="center">
          <header>
            <Stack gap={2.25} direction="row" align="center">
              <Link
                className={css({
                  display: 'flex',
                  flexDirection: { base: 'column', sm: 'row' },
                  alignItems: 'start',
                  gap: { base: '0', sm: '2rem' },
                  padding: '0.25rem',
                  _hover: {
                    backgroundColor: 'muted',
                    borderRadius: '4px',
                  },
                })}
                onClick={(event) => {
                  if (
                    isRoom &&
                    !window.confirm(t('leaveRoomPrompt', { ns: 'rooms' }))
                  ) {
                    event.preventDefault()
                  }
                }}
                to="/"
              >
                {/* this is there only as a hook for custom CSS users who might want to show something before the app logo */}
                <div
                  className={`Header-beforeLogo ${css({
                    display: 'none',
                  })}`}
                />
                <HStack gap={0}>
                  <Logo />
                </HStack>
              </Link>
            </Stack>
          </header>
          <nav>
            <Stack gap={1} direction="row" align="center">
              {isLoggedIn === false &&
                !isHome &&
                !isLegalTerms &&
                !isAccessibility &&
                !isTermsOfService &&
                !loginButtonDisabledByUrl && (
                  <>
                    <div
                      className={css({
                        display: { base: 'none', xsm: 'block' },
                      })}
                    >
                      <LoginButton proConnectHint={false} size="sm" />
                    </div>
                    <LoginHint />
                  </>
                )}
              {!!user && (
                <Menu>
                  <Button size="sm" variant="secondaryText">
                    <VisualOnlyTooltip
                      tooltip={loggedInTooltip}
                      ariaLabel={loggedInAriaLabel}
                      tooltipPosition="bottom"
                    >
                      <span
                        className={css({
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          maxWidth: '350px',
                          display: { base: 'none', xsm: 'block' },
                        })}
                      >
                        {user?.full_name || user?.email}
                      </span>
                    </VisualOnlyTooltip>
                  </Button>
                  <MenuList
                    variant={'light'}
                    items={[{ value: 'logout', label: t('logout') }]}
                    onAction={(value) => {
                      if (value === 'logout') {
                        logout()
                      }
                    }}
                  />
                </Menu>
              )}
              <SettingsButton />
            </Stack>
          </nav>
        </HStack>
      </div>
    </>
  )
}
