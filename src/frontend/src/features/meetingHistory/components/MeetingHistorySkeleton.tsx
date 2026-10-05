import { css } from '@/styled-system/css'
import { skeletonBar as bar } from './skeleton'

export const MeetingHistorySkeleton = ({
  label,
  rows = 4,
}: {
  label: string
  rows?: number
}) => (
  <div role="status" aria-live="polite">
    <span className={css({ srOnly: true })}>{label}</span>
    <div
      aria-hidden="true"
      className={css({
        border: '1px solid token(colors.border)',
        borderRadius: '12px',
        overflow: 'hidden',
      })}
    >
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          className={css({
            display: 'flex',
            flexDirection: 'column',
            gap: '0.5rem',
            padding: '1rem',
            '&:not(:last-child)': {
              borderBottom: '1px solid token(colors.border)',
            },
          })}
        >
          <span
            className={bar}
            style={{ width: `${55 - index * 6}%`, height: 14 }}
          />
          <span className={bar} style={{ width: '38%', height: 10 }} />
        </div>
      ))}
    </div>
  </div>
)
