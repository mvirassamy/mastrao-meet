import type { CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import { SECTION_ICONS, type MeetingContentKind } from './meetingContent'
import {
  detailHeader,
  detailSections,
  sectionBody,
  sectionCard,
  sectionHeader,
  sectionTitle,
} from './meetingDetailLayout'
import { skeletonBar } from './skeleton'

const Bar = ({ width, height }: { width: string; height: number }) => (
  <span className={skeletonBar} style={{ width, height }} />
)

const stack = (gap: CSSProperties['gap']) =>
  css({ display: 'flex', flexDirection: 'column', gap })

/** The real section header, greyed, so the page keeps its shape. */
const SkeletonSection = ({
  kind,
  children,
}: {
  kind: MeetingContentKind
  children: React.ReactNode
}) => {
  const { t } = useTranslation('meetingHistory')

  return (
    <div className={sectionCard}>
      <div className={sectionHeader({ look: 'pending' })}>
        <img src={SECTION_ICONS[kind]} alt="" width={96} height={96} />
        <p className={sectionTitle}>{t(`${kind}.title`)}</p>
      </div>
      <div className={sectionBody}>{children}</div>
    </div>
  )
}

const SummaryPlaceholder = () => (
  <div className={stack('1rem')}>
    <Bar width="70%" height={12} />
    <div className={stack('0.5rem')}>
      <Bar width="100%" height={12} />
      <Bar width="94%" height={12} />
      <Bar width="62%" height={12} />
    </div>
    <div className={stack('0.5rem')}>
      <Bar width="35%" height={14} />
      <Bar width="88%" height={12} />
      <Bar width="76%" height={12} />
    </div>
  </div>
)

const TRANSCRIPT_TURNS = [
  ['92%', '58%'],
  ['84%', '0%'],
  ['96%', '70%'],
] as const

const TranscriptPlaceholder = () => (
  <div className={stack('1rem')}>
    {TRANSCRIPT_TURNS.map(([first, second], index) => (
      <div key={index} className={stack('0.375rem')}>
        <div className={css({ display: 'flex', gap: '0.5rem' })}>
          <Bar width="5.5rem" height={12} />
          <Bar width="2.5rem" height={12} />
        </div>
        <Bar width={first} height={12} />
        {second !== '0%' && <Bar width={second} height={12} />}
      </div>
    ))}
  </div>
)

/**
 * Loading state of the meeting detail with the page's own shape: title,
 * date line, then the Summary and Transcript cards.
 */
export const MeetingHistoryDetailSkeleton = ({ label }: { label: string }) => (
  <div role="status" aria-live="polite">
    <span className={css({ srOnly: true })}>{label}</span>
    <div aria-hidden="true">
      <div className={detailHeader}>
        {/* Same heights as the title and date line, so nothing moves. */}
        <Bar width="min(26rem, 70%)" height={32} />
        <div
          className={css({
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            columnGap: '1rem',
            rowGap: '0.25rem',
            minHeight: '20px',
            marginTop: '0.5rem',
          })}
        >
          <Bar width="11rem" height={14} />
          <Bar width="8rem" height={14} />
          <Bar width="6rem" height={14} />
        </div>
      </div>
      <div className={detailSections}>
        <SkeletonSection kind="summary">
          <SummaryPlaceholder />
        </SkeletonSection>
        <SkeletonSection kind="transcript">
          <TranscriptPlaceholder />
        </SkeletonSection>
      </div>
    </div>
  </div>
)
