import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '@/api/ApiError'
import { LoginButton } from '@/components/LoginButton'
import { MeetingHistoryDetailSkeleton } from '@/features/meetingHistory/components/MeetingHistoryDetailSkeleton'
import { MeetingHistoryStatePanel } from '@/features/meetingHistory/components/MeetingHistoryStatePanel'
import { RetryIcon } from '@/icons'
import { Screen } from '@/layout/Screen'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import { readHostResult, type HostResult } from '../api/readHostResult'
import { HostResultDetail } from '../components/HostResultDetail'
import { HostResultHeader } from '../components/HostResultHeader'
import { consumeHostResultFragment } from '../utils/hostResultFragment'

type PageState =
  | { status: 'loading' }
  | { status: 'unavailable' }
  | { status: 'temporary-error' }
  | { status: 'ready'; result: HostResult }

/** Visitor page opened from the host's e-mail link, without a Meet account. */
const HostResultPage = () => {
  const { t } = useTranslation('global', { keyPrefix: 'hostResult' })
  const access = consumeHostResultFragment()
  const started = useRef(false)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const [state, setState] = useState<PageState>({ status: 'loading' })

  const load = useCallback(async () => {
    if (!access) {
      setState({ status: 'unavailable' })
      return
    }
    setState({ status: 'loading' })
    try {
      setState({ status: 'ready', result: await readHostResult(access) })
    } catch (error) {
      const unavailable = error instanceof ApiError && error.statusCode === 404
      setState({ status: unavailable ? 'unavailable' : 'temporary-error' })
    }
  }, [access])

  useEffect(() => {
    if (started.current) return
    started.current = true
    void load()
  }, [load])

  // Moves keyboard and screen reader focus to the page heading once known.
  useEffect(() => {
    if (state.status !== 'loading')
      headingRef.current?.focus({ preventScroll: true })
  }, [state.status])

  let content: ReactNode
  switch (state.status) {
    case 'loading':
      content = (
        <MeetingHistoryDetailSkeleton label={t('loading')} tabCount={2} />
      )
      break
    case 'unavailable':
      content = (
        <MeetingHistoryStatePanel
          role="alert"
          headingLevel={1}
          headingRef={headingRef}
          illustration="/assets/illustrations/lien-expire.webp"
          title={t('unavailable.title')}
          description={t('unavailable.description')}
          action={
            <LoginButton
              proConnectHint={false}
              returnTo={`${window.location.origin}/`}
            />
          }
        />
      )
      break
    case 'temporary-error':
      content = (
        <MeetingHistoryStatePanel
          role="alert"
          headingLevel={1}
          headingRef={headingRef}
          tone="danger"
          illustration="/assets/illustrations/reunion-detail-erreur.webp"
          title={t('temporaryError.title')}
          description={t('temporaryError.description')}
          action={
            <Button
              variant="secondary"
              icon={<RetryIcon aria-hidden="true" />}
              onPress={() => void load()}
            >
              {t('retry')}
            </Button>
          }
        />
      )
      break
    case 'ready':
      content = (
        <HostResultDetail result={state.result} headingRef={headingRef} />
      )
      break
  }

  return (
    <Screen header={false} footer={false}>
      {/* Same white palette as the signed-in meeting history detail. */}
      <div
        className={`authenticated-meet-workspace ${css({
          display: 'flex',
          flexDirection: 'column',
          flex: 1,
          backgroundColor: 'card',
          color: 'foreground',
        })}`}
      >
        <HostResultHeader />
        <div
          className={css({
            width: '100%',
            maxWidth: '48rem',
            marginX: 'auto',
            padding: { base: '1.75rem 1rem 3rem', md: '2.5rem 1.5rem 4rem' },
          })}
        >
          {content}
        </div>
      </div>
    </Screen>
  )
}

export default HostResultPage
