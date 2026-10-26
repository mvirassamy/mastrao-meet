import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/primitives'
import {
  fetchNativeFull,
  nativeFullPreviewRequest,
  type MeetingNativeFull,
  type NativeFullRequest,
  type NativeFullSnapshot,
} from '../api/meetingNativeFull'
import type { MeetingTranscript } from '../api/types'
import { MeetingTranscriptContent } from './MeetingTranscriptContent'

/** A fresh identity boundary also invalidates in-flight reads and loaded text. */
export function MeetingNativeFullTranscript({
  meetingRef,
  transcript,
  snapshot,
  children,
}: {
  meetingRef?: string
  transcript: MeetingTranscript
  snapshot?: NativeFullSnapshot
  children: ReactNode
}) {
  if (!meetingRef) return <>{children}</>
  const request = nativeFullPreviewRequest(meetingRef, transcript, snapshot)
  if (!request) return <>{children}</>
  const identity = JSON.stringify([
    request,
    snapshot?.previewIdentity,
    transcript.projection,
    transcript.segments,
  ])
  return (
    <NativeFullReader key={identity} request={request} preview={children} />
  )
}

type ReadState =
  | { status: 'idle' | 'loading' | 'failed' | 'expired' }
  | { status: 'loaded'; full: MeetingNativeFull }

const buttonLabel = (status: ReadState['status']) => {
  switch (status) {
    case 'loading':
      return 'transcript.native.full.loading'
    case 'failed':
    case 'expired':
      return 'transcript.native.full.retry'
    default:
      return 'transcript.native.full.load'
  }
}

function NativeFullReader({
  request,
  preview,
}: {
  request: NativeFullRequest
  preview: ReactNode
}) {
  const { t } = useTranslation('meetingHistory')
  const [state, setState] = useState<ReadState>({ status: 'idle' })
  const pending = useRef<AbortController>()

  useEffect(() => () => pending.current?.abort(), [])

  useEffect(() => {
    if (state.status !== 'loaded') return
    let timer: ReturnType<typeof setTimeout>
    const expire = () => {
      const remaining =
        (state.full.retentionExpiresAt - Date.now() / 1000) * 1000
      if (remaining <= 0) {
        setState({ status: 'expired' })
        return
      }
      timer = setTimeout(expire, Math.min(remaining, 2_147_483_647))
    }
    expire()
    return () => clearTimeout(timer)
  }, [state])

  const load = async () => {
    if (pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setState({ status: 'loading' })
    try {
      const full = await fetchNativeFull(request, controller.signal)
      if (!controller.signal.aborted) setState({ status: 'loaded', full })
    } catch {
      if (!controller.signal.aborted) setState({ status: 'failed' })
    } finally {
      if (pending.current === controller) pending.current = undefined
    }
  }

  if (state.status === 'loaded') {
    return (
      <section aria-label={t('transcript.native.full.title')}>
        <h3>{t('transcript.native.full.title')}</h3>
        <MeetingTranscriptContent
          segments={state.full.segments}
          truncated={false}
          nativeCoverage={state.full.coverage}
          partialNotice={t('transcript.native.full.scope')}
        />
      </section>
    )
  }

  const label = t(buttonLabel(state.status))
  return (
    <>
      {preview}
      {state.status === 'failed' && (
        <p role="alert">{t('transcript.native.full.failed')}</p>
      )}
      {state.status === 'expired' && (
        <p role="status">{t('transcript.native.full.expired')}</p>
      )}
      <Button
        variant="outline"
        size="sm"
        isDisabled={state.status === 'loading'}
        onPress={() => void load()}
      >
        {label}
      </Button>
    </>
  )
}
