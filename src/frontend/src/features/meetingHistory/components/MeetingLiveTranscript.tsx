import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import type { MeetingLiveEvent, MeetingLiveSource } from '../api/types'
import { MeetingContentToolbar } from './MeetingContentToolbar'

const groupSourceLegs = (events: MeetingLiveEvent[]) => {
  const legs = new Map<string, MeetingLiveEvent[]>()
  for (const event of events) {
    const leg = legs.get(event.leg_ref)
    if (leg) leg.push(event)
    else legs.set(event.leg_ref, [event])
  }
  return Array.from(legs, ([legRef, events]) => ({ legRef, events }))
}

const LiveEvent = ({ event }: { event: MeetingLiveEvent }) => {
  const { t } = useTranslation('meetingHistory')
  switch (event.kind) {
    case 'final':
      return (
        <p
          lang={event.language ?? undefined}
          className={css({
            margin: 0,
            whiteSpace: 'pre-wrap',
            overflowWrap: 'anywhere',
          })}
        >
          {event.text}
        </p>
      )
    case 'gap':
      return <p>{t(`transcript.live.gap.${event.reason}`)}</p>
    case 'end':
      return <p>{t(`transcript.live.end.${event.reason}`)}</p>
  }
}

export function MeetingLiveTranscript({
  sources,
  truncated,
  retained = false,
}: {
  sources: MeetingLiveSource[]
  truncated: boolean
  retained?: boolean
}) {
  const { t } = useTranslation('meetingHistory')
  const title = retained
    ? 'transcript.live.full.title'
    : 'transcript.live.title'
  const copyText = () => {
    const lines = [t('transcript.live.partial')]
    for (const [index, source] of sources.entries()) {
      lines.push(t('transcript.live.source', { number: index + 1 }))
      lines.push(t(`transcript.live.state.${source.state}`))
      if (source.incomplete) lines.push(t('transcript.live.incomplete'))
      for (const [legIndex, leg] of groupSourceLegs(source.events).entries()) {
        lines.push(t('transcript.live.leg', { number: legIndex + 1 }))
        for (const event of leg.events) {
          if (event.kind === 'final') lines.push(event.text)
          else lines.push(t(`transcript.live.${event.kind}.${event.reason}`))
        }
      }
    }
    return lines.join('\n\n')
  }
  return (
    <section
      aria-label={t(title)}
      className={css({
        marginTop: '1rem',
        fontSize: '0.9375rem',
        lineHeight: '1.5rem',
      })}
    >
      <h3>{t(title)}</h3>
      {retained ? (
        <MeetingContentToolbar
          note={t('transcript.live.partial')}
          copyText={copyText()}
        />
      ) : (
        <p>{t('transcript.live.partial')}</p>
      )}
      {truncated && <p>{t('transcript.live.truncated')}</p>}
      {sources.map((source, sourceIndex) => (
        <section
          key={source.sourceRef}
          data-source-ref={source.sourceRef}
          aria-label={t('transcript.live.source', { number: sourceIndex + 1 })}
          className={css({
            borderTop: '1px solid token(colors.border)',
            paddingY: '0.75rem',
          })}
        >
          <h4>
            {t('transcript.live.source', { number: sourceIndex + 1 })}
            {' — '}
            {t(`transcript.live.participant.${source.participantKind}`)}
          </h4>
          <p>{t(`transcript.live.state.${source.state}`)}</p>
          {source.incomplete && <p>{t('transcript.live.incomplete')}</p>}
          {groupSourceLegs(source.events).map((leg, legIndex) => (
            <section
              key={leg.legRef}
              data-leg-ref={leg.legRef}
              aria-label={t('transcript.live.leg', { number: legIndex + 1 })}
            >
              <h5>{t('transcript.live.leg', { number: legIndex + 1 })}</h5>
              <ol className={css({ listStyle: 'none', padding: 0 })}>
                {leg.events.map((event) => (
                  <li key={event.sequence}>
                    <LiveEvent event={event} />
                  </li>
                ))}
              </ol>
            </section>
          ))}
        </section>
      ))}
    </section>
  )
}
