import { css } from '@/styled-system/css'
import { detailHeader } from './meetingDetailLayout'
import { skeletonBar } from './skeleton'

const Bar = ({
  width,
  height,
  round = false,
}: {
  width: string
  height: number
  round?: boolean
}) => (
  <span
    className={skeletonBar}
    style={{ width, height, borderRadius: round ? 999 : undefined }}
  />
)

/** Rubrics of the summary sheet: label width, then its content lines. */
const SHEET_ROWS = [
  ['4rem', ['92%', '70%']],
  ['5rem', ['78%', '60%']],
  ['4rem', ['84%', '52%']],
] as const

const sheetRow = css({
  display: 'grid',
  gridTemplateColumns: { base: 'minmax(0, 1fr)', sm: '8rem minmax(0, 1fr)' },
  columnGap: '1.25rem',
  rowGap: '0.5rem',
  paddingY: '0.875rem',
  borderBottom: '1px solid token(colors.border)',
})

/**
 * Loading state of the meeting detail with the page's own shape: title,
 * date line, the pill tabs, then the summary sheet.
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
      <div
        className={css({
          display: 'flex',
          gap: '0.25rem',
          paddingBottom: '0.75rem',
          borderBottom: '1px solid token(colors.border)',
        })}
      >
        <Bar width="6.5rem" height={36} round />
        <Bar width="8rem" height={36} round />
        <Bar width="6.5rem" height={36} round />
      </div>
      <div className={css({ marginTop: '1rem' })}>
        <Bar width="14rem" height={14} />
        <div
          className={css({
            marginTop: '0.75rem',
            borderTop: '1px solid token(colors.border)',
          })}
        >
          {SHEET_ROWS.map(([labelWidth, lines], index) => (
            <div key={index} className={sheetRow}>
              <Bar width={labelWidth} height={12} />
              <div
                className={css({
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '0.5rem',
                })}
              >
                {lines.map((width) => (
                  <Bar key={width} width={width} height={12} />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  </div>
)
