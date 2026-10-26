import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { MeetingTranscript } from '../api/types'
import { MeetingLiveFullTranscript } from './MeetingLiveFullTranscript'
import { MeetingLiveObservedDomain } from './MeetingLiveObservedDomain'

type Props = { transcript: MeetingTranscript; meetingRef?: string }

/** A fresh projection replaces any loaded reader; absence also withdraws it. */
export function MeetingLiveResults(props: Props) {
  const domain = props.transcript.liveObservedDomain
  const identity = JSON.stringify([
    domain?.projectionDigest,
    domain?.retentionExpiresAt,
  ])
  return <RetainedLiveResults key={identity} {...props} />
}

function RetainedLiveResults({ transcript, meetingRef }: Props) {
  const { t } = useTranslation('meetingHistory')
  const domain = transcript.liveObservedDomain
  const deadline = domain?.retentionExpiresAt
  const [expired, setExpired] = useState(
    () => deadline !== undefined && deadline * 1000 <= Date.now()
  )
  useEffect(() => {
    if (deadline === undefined) return
    let timer: ReturnType<typeof setTimeout>
    const check = () => {
      const remaining = deadline * 1000 - Date.now()
      if (remaining <= 0) {
        setExpired(true)
        return
      }
      timer = setTimeout(check, Math.min(remaining, 2_147_483_647))
    }
    check()
    return () => clearTimeout(timer)
  }, [deadline])
  if (expired) {
    return <p role="status">{t('transcript.live.domain.expired')}</p>
  }
  return (
    <>
      {domain && <MeetingLiveObservedDomain domain={domain} />}
      {(transcript.liveSources?.length ?? 0) > 0 && (
        <MeetingLiveFullTranscript
          meetingRef={meetingRef}
          transcript={transcript}
        />
      )}
    </>
  )
}
