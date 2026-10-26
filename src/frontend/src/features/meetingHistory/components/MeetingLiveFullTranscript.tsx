import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/primitives'
import {
  fetchLiveFull,
  liveFullPreviewRequest,
  type LiveFullRequest,
  type MeetingLiveFull,
} from '../api/meetingLiveFull'
import type { MeetingTranscript } from '../api/types'
import { MeetingLiveTranscript } from './MeetingLiveTranscript'

/** A fresh wire identity unmounts loaded text and aborts outstanding reads. */
export function MeetingLiveFullTranscript({
  meetingRef,
  transcript,
}: {
  meetingRef?: string
  transcript: MeetingTranscript
}) {
  const preview = (
    <MeetingLiveTranscript
      sources={transcript.liveSources ?? []}
      truncated={transcript.liveTruncated === true}
    />
  )
  if (!meetingRef) return preview
  const request = liveFullPreviewRequest(meetingRef, transcript)
  if (!request) return preview
  return (
    <LiveFullReader
      key={request.previewIdentity}
      request={request}
      preview={preview}
    />
  )
}

type ReadState =
  | { status: 'idle' | 'loading' | 'failed' | 'expired' }
  | { status: 'loaded'; full: MeetingLiveFull }

const buttonLabel = (status: ReadState['status']) => {
  switch (status) {
    case 'loading':
      return 'transcript.live.full.loading'
    case 'failed':
    case 'expired':
      return 'transcript.live.full.retry'
    default:
      return 'transcript.live.full.load'
  }
}

function LiveFullReader({
  request,
  preview,
}: {
  request: LiveFullRequest
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
      const remaining = state.full.retentionExpiresAt * 1000 - Date.now()
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
      const full = await fetchLiveFull(request, controller.signal)
      if (!controller.signal.aborted) setState({ status: 'loaded', full })
    } catch {
      if (!controller.signal.aborted) setState({ status: 'failed' })
    } finally {
      if (pending.current === controller) pending.current = undefined
    }
  }

  if (state.status === 'loaded')
    return (
      <MeetingLiveTranscript
        sources={state.full.sources}
        truncated={false}
        retained
      />
    )
  return (
    <>
      {preview}
      {state.status === 'failed' && (
        <p role="alert">{t('transcript.live.full.failed')}</p>
      )}
      {state.status === 'expired' && (
        <p role="status">{t('transcript.live.full.expired')}</p>
      )}
      <Button
        variant="outline"
        size="sm"
        isDisabled={state.status === 'loading'}
        onPress={() => void load()}
      >
        {t(buttonLabel(state.status))}
      </Button>
    </>
  )
}
