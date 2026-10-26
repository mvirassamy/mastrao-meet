import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { GroupIcon } from '@/icons'
import { css } from '@/styled-system/css'
import type {
  MeetingNativeCoverage as NativeCoverage,
  MeetingNativePartial,
  MeetingTranscriptSegment,
} from '../api/types'
import { formatTranscriptTimestamp } from '../utils/meetingHistoryFormat'
import { MeetingContentToolbar } from './MeetingContentToolbar'

const bodyTextClass = css({
  margin: 0,
  color: 'foreground',
  fontSize: '0.9375rem',
  lineHeight: '1.5rem',
  overflowWrap: 'anywhere',
})

export const MeetingNativePartialTranscript = ({
  partial,
  coverage,
}: {
  partial: MeetingNativePartial
  coverage?: NativeCoverage
}) => {
  const { t } = useTranslation('meetingHistory')
  return (
    <section aria-label={t('transcript.native.partialTitle')}>
      <h3>{t('transcript.native.partialTitle')}</h3>
      <MeetingTranscriptContent
        segments={partial.segments}
        truncated={partial.truncated}
        nativeCoverage={coverage}
        partialNotice={t('transcript.native.partialNotice')}
      />
    </section>
  )
}

type SpeakerTurn = {
  id: string
  speaker: string | null
  startMs: number
  texts: string[]
  sourceEpoch: MeetingTranscriptSegment['sourceEpoch']
}

const groupTurns = (segments: MeetingTranscriptSegment[]) =>
  segments.reduce<SpeakerTurn[]>((turns, segment) => {
    const previous = turns[turns.length - 1]
    if (
      previous &&
      previous.speaker === segment.speaker &&
      previous.sourceEpoch?.epochRef === segment.sourceEpoch?.epochRef
    )
      previous.texts.push(segment.text)
    else
      turns.push({
        id: segment.sourceEpoch
          ? `${segment.sourceEpoch.epochRef}:${segment.id}`
          : segment.id,
        speaker: segment.speaker,
        startMs: segment.startMs,
        texts: [segment.text],
        sourceEpoch: segment.sourceEpoch,
      })
    return turns
  }, [])

/** Each speaker keeps one colour along the transcript, in order of appearance. */
const SPEAKER_COLORS = ['#2d5be3', '#0f766e', '#9333ea', '#c2410c', '#0369a1']
const UNKNOWN_SPEAKER_COLOR = '#98a2b3'

const speakerColors = (turns: SpeakerTurn[]) => {
  const colors = new Map<string, string>()
  for (const turn of turns) {
    if (turn.speaker === null || colors.has(turn.speaker)) continue
    colors.set(
      turn.speaker,
      SPEAKER_COLORS[colors.size % SPEAKER_COLORS.length]
    )
  }
  return colors
}

/** The transcript as plain text: speaker and time, then what was said. */
const transcriptPlainText = (turns: SpeakerTurn[], unknownSpeaker: string) =>
  turns
    .map(
      (turn) =>
        `${turn.speaker ?? unknownSpeaker} (${formatTranscriptTimestamp(turn.startMs)})\n${turn.texts.join('\n')}`
    )
    .join('\n\n')

type SourceTurnGroup = {
  label: string
  turns: SpeakerTurn[]
}

/** Keep source boundaries and input order, including a local clock restart. */
const groupSourceTurns = (turns: SpeakerTurn[]) => {
  const groups: { epochRef: string | undefined; turns: SpeakerTurn[] }[] = []
  for (const turn of turns) {
    const epochRef = turn.sourceEpoch?.epochRef
    const previous = groups[groups.length - 1]
    if (previous && previous.epochRef === epochRef) previous.turns.push(turn)
    else groups.push({ epochRef, turns: [turn] })
  }
  return groups
}

const labelSourceGroups = (turns: SpeakerTurn[], t: TFunction) => {
  const sourceNumbers = new Map<string, number>()
  return groupSourceTurns(turns).map((group): SourceTurnGroup => {
    let label = t('transcript.native.unknownSource')
    if (group.epochRef !== undefined) {
      if (!sourceNumbers.has(group.epochRef))
        sourceNumbers.set(group.epochRef, sourceNumbers.size + 1)
      label = t('transcript.native.source', {
        number: sourceNumbers.get(group.epochRef),
      })
    }
    return { label, turns: group.turns }
  })
}

const turnTimestamp = (
  turn: SpeakerTurn,
  localTimes: boolean,
  t: TFunction
) => {
  if (!localTimes) return formatTranscriptTimestamp(turn.startMs)
  if (!turn.sourceEpoch) return t('transcript.native.unknownTimeBasis')
  return t('transcript.native.localTime', {
    time: formatTranscriptTimestamp(turn.startMs),
  })
}

const nativeTranscriptPlainText = (
  groups: SourceTurnGroup[],
  unknownSpeaker: string,
  t: TFunction
) =>
  [
    t('transcript.native.localTimes'),
    ...groups.map((group) =>
      [
        group.label,
        ...group.turns.map(
          (turn) =>
            `${turn.speaker ?? unknownSpeaker} (${turnTimestamp(turn, true, t)})\n${turn.texts.join('\n')}`
        ),
      ].join('\n\n')
    ),
  ].join('\n\n')

export const MeetingTranscriptContent = ({
  segments,
  truncated,
  nativeCoverage,
  partialNotice,
}: {
  segments: MeetingTranscriptSegment[]
  truncated: boolean
  nativeCoverage?: NativeCoverage
  partialNotice?: string
}) => {
  const { t } = useTranslation('meetingHistory')
  const turns = groupTurns(segments)
  const colors = speakerColors(turns)
  const unknownSpeaker = t('transcript.unknownSpeaker')
  const hasLocalTimes = turns.some((turn) => turn.sourceEpoch !== undefined)
  const groups = labelSourceGroups(turns, t)
  let copyText = transcriptPlainText(turns, unknownSpeaker)
  if (hasLocalTimes)
    copyText = nativeTranscriptPlainText(groups, unknownSpeaker, t)
  if (hasLocalTimes && nativeCoverage)
    copyText = `${t('transcript.native.coverage.unverified')}\n\n${copyText}`
  if (partialNotice) {
    const notices = [partialNotice, copyText]
    if (truncated) notices.push(t('transcript.truncated'))
    copyText = notices.join('\n\n')
  }
  return (
    <>
      {partialNotice && <p className={bodyTextClass}>{partialNotice}</p>}
      <MeetingContentToolbar
        // Unnamed speakers cannot be counted, so no count is shown for them.
        note={
          colors.size > 0 && (
            <>
              <GroupIcon size={15} aria-hidden="true" />
              {t('transcript.speakers', { count: colors.size })}
            </>
          )
        }
        copyText={copyText}
      />
      {hasLocalTimes ? (
        <NativeTranscriptGroups
          groups={groups}
          colors={colors}
          unknownSpeaker={unknownSpeaker}
        />
      ) : (
        <TranscriptTurnList
          turns={turns}
          colors={colors}
          unknownSpeaker={unknownSpeaker}
          localTimes={false}
        />
      )}
      {truncated && (
        <p
          className={css({
            marginTop: '1rem',
            marginBottom: 0,
            color: 'muted-foreground',
            fontSize: '0.8125rem',
          })}
        >
          {t('transcript.truncated')}
        </p>
      )}
    </>
  )
}

const NativeTranscriptGroups = ({
  groups,
  colors,
  unknownSpeaker,
}: {
  groups: SourceTurnGroup[]
  colors: Map<string, string>
  unknownSpeaker: string
}) => {
  const { t } = useTranslation('meetingHistory')
  return (
    <div>
      <p className={css({ color: 'muted-foreground', fontSize: '0.8125rem' })}>
        {t('transcript.native.localTimes')}
      </p>
      {groups.map((group, index) => (
        <section
          key={index}
          aria-label={group.label}
          className={css({
            borderTop: '1px solid token(colors.border)',
            paddingY: '0.875rem',
          })}
        >
          <h3
            className={css({
              margin: 0,
              marginBottom: '0.75rem',
              fontSize: '0.875rem',
              fontWeight: 600,
            })}
          >
            {group.label}
          </h3>
          <TranscriptTurnList
            turns={group.turns}
            colors={colors}
            unknownSpeaker={unknownSpeaker}
            localTimes
          />
        </section>
      ))}
    </div>
  )
}

const TranscriptTurnList = ({
  turns,
  colors,
  unknownSpeaker,
  localTimes,
}: {
  turns: SpeakerTurn[]
  colors: Map<string, string>
  unknownSpeaker: string
  localTimes: boolean
}) => {
  const { t } = useTranslation('meetingHistory')
  return (
    <ol
      className={css({
        display: 'flex',
        flexDirection: 'column',
        gap: '1.25rem',
        margin: 0,
        padding: 0,
        listStyle: 'none',
      })}
    >
      {turns.map((turn) => {
        // An unnamed speaker keeps the default text colour for contrast.
        const color = turn.speaker ? colors.get(turn.speaker) : undefined
        return (
          <li
            key={turn.id}
            style={{ borderLeftColor: color ?? UNKNOWN_SPEAKER_COLOR }}
            className={css({
              paddingLeft: '0.875rem',
              borderLeft: '3px solid',
            })}
          >
            <p
              className={css({
                display: 'flex',
                alignItems: 'baseline',
                gap: '0.5rem',
                margin: 0,
                marginBottom: '0.125rem',
                fontSize: '0.8125rem',
                lineHeight: '1.25rem',
              })}
            >
              <span style={{ color }} className={css({ fontWeight: 600 })}>
                {turn.speaker ?? unknownSpeaker}
              </span>
              <span
                className={css({
                  color: 'muted-foreground',
                  fontVariantNumeric: 'tabular-nums',
                })}
              >
                {turnTimestamp(turn, localTimes, t)}
              </span>
            </p>
            {turn.texts.map((text, index) => (
              <p
                key={index}
                className={`${bodyTextClass} ${css({ '& + &': { marginTop: '0.375rem' } })} ${localTimes ? css({ whiteSpace: 'pre-wrap' }) : ''}`}
              >
                {text}
              </p>
            ))}
          </li>
        )
      })}
    </ol>
  )
}
