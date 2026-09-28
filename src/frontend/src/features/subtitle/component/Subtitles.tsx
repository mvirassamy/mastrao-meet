import { useMemo } from 'react'
import { useSubtitles } from '../hooks/useSubtitles'
import { css, cva } from '@/styled-system/css'
import { styled } from '@/styled-system/jsx'
import { Avatar } from '@/components/Avatar'
import { Text } from '@/primitives'
import { useRoomContext } from '@livekit/components-react'
import { getParticipantName } from '@/features/rooms/utils/getParticipantName'
import type { Participant } from 'livekit-client'
import { useSnapshot } from 'valtio'
import {
  accessibilityStore,
  CAPTION_TEXT_SIZE_OPTIONS,
  CAPTION_FONT_COLOR_VALUES,
  CAPTION_BACKGROUND_COLOR_VALUES,
  type CaptionTextSize,
} from '@/stores/accessibility'
import { getParticipantForTranscription, useLiveTranscription } from '../store'
import {
  DEFAULT_PARTICIPANT_COLOR,
  getParticipantColor,
} from '@/features/rooms/utils/getParticipantColor'
const FONT_SIZE_CONFIG: Record<
  CaptionTextSize,
  { fontSize: string; lineHeight: string }
> = {
  small: { fontSize: '0.875rem', lineHeight: '1.2rem' },
  medium: { fontSize: '1.5rem', lineHeight: '1.7rem' },
  large: { fontSize: '2.25rem', lineHeight: '2.5rem' },
}

const CAPTION_FONT_SIZES = Object.fromEntries(
  CAPTION_TEXT_SIZE_OPTIONS.map((size) => [size, FONT_SIZE_CONFIG[size]])
) as Record<CaptionTextSize, { fontSize: string; lineHeight: string }>

export interface TranscriptionRow {
  id: string
  participantIdentity: string
  participant?: Participant
  segments: string[]
}

const Transcription = ({ row }: { row: TranscriptionRow }) => {
  const { captionTextSize, captionFontColor, captionBackgroundColor } =
    useSnapshot(accessibilityStore)
  const participantName = row.participant
    ? getParticipantName(row.participant)
    : row.participantIdentity
  const participantColor = row.participant
    ? getParticipantColor(row.participant)
    : DEFAULT_PARTICIPANT_COLOR
  const { fontSize, lineHeight } = CAPTION_FONT_SIZES[captionTextSize]
  const fontColor = CAPTION_FONT_COLOR_VALUES[captionFontColor]
  const backgroundColor =
    CAPTION_BACKGROUND_COLOR_VALUES[captionBackgroundColor]

  const displayText = row.segments.join(' ')

  if (!displayText) return null

  return (
    <div
      className={css({
        maxWidth: '800px',
        width: '100%',
      })}
    >
      <div
        className={css({
          display: 'flex',
          gap: '0.5rem',
        })}
      >
        <Avatar
          name={participantName}
          bgColor={participantColor}
          context="subtitles"
        />
        <div
          className={css({
            width: '100%',
          })}
          style={{ color: fontColor }}
        >
          <Text variant="h3" margin={false}>
            {participantName}
          </Text>
          <p
            className={css({
              fontWeight: '400',
              borderRadius: '4px',
              padding: '0.125rem 0.25rem',
            })}
            style={{ fontSize, lineHeight, backgroundColor }}
          >
            {displayText}
          </p>
        </div>
      </div>
    </div>
  )
}

const SubtitlesWrapper = styled(
  'div',
  cva({
    base: {
      width: '100%',
      paddingTop: 'var(--lk-grid-gap)',
      transition: 'height .5s cubic-bezier(0.4,0,0.2,1) 5ms',
    },
    variants: {
      areOpen: {
        true: {
          height: '12rem',
        },
        false: {
          height: '0',
        },
      },
    },
  })
)

export const Subtitles = () => {
  const { areSubtitlesOpen } = useSubtitles()
  const room = useRoomContext()
  const { segments: transcriptionSegments } = useLiveTranscription()

  const transcriptionRows = useMemo(() => {
    if (transcriptionSegments.length === 0) return []

    const rows: TranscriptionRow[] = []
    let currentRow: TranscriptionRow | null = null

    for (const segment of transcriptionSegments) {
      const shouldStartNewRow =
        !currentRow ||
        currentRow.participantIdentity !== segment.participantIdentity

      if (shouldStartNewRow) {
        const participant = getParticipantForTranscription(
          room,
          segment.participantIdentity
        )
        currentRow = {
          id: `${segment.participantIdentity}-${segment.sequence}`,
          participantIdentity: segment.participantIdentity,
          participant,
          segments: [segment.text],
        }
        rows.push(currentRow)
      } else if (currentRow) {
        currentRow.segments.push(segment.text)
      }
    }
    return rows
  }, [room, transcriptionSegments])

  return (
    <SubtitlesWrapper areOpen={areSubtitlesOpen}>
      <div
        className={css({
          height: '100%',
          width: '100%',
          display: 'flex',
          gap: '1.25rem',
          flexDirection: 'column-reverse',
          overflowAnchor: 'auto',
          overflowY: 'scroll',
          padding: '0 1rem',
          alignItems: 'center',
        })}
      >
        {transcriptionRows
          .slice()
          .reverse()
          .map((row) => (
            <Transcription key={row.id} row={row} />
          ))}
      </div>
    </SubtitlesWrapper>
  )
}
