import { css } from '@/styled-system/css'

/*
 * Layout of the meeting detail, shared by the page and its loading
 * skeleton so both have the same shape.
 */

export const detailHeader = css({ marginBottom: '1.5rem' })

export const detailTitle = css({
  margin: 0,
  fontSize: { base: '1.375rem', md: '1.625rem' },
  lineHeight: 1.25,
  fontWeight: 600,
  letterSpacing: '-0.025em',
  overflowWrap: 'anywhere',
})

/** Date, time range and participants under the title. */
export const detailMeta = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'center',
  columnGap: '1rem',
  rowGap: '0.25rem',
  marginTop: '0.5rem',
  marginBottom: 0,
  color: 'muted-foreground',
  fontSize: '0.8125rem',
  lineHeight: '1.25rem',
})

export const detailMetaItem = css({
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.375rem',
})

export const sectionCard = css({
  minWidth: 0,
  border: '1px solid token(colors.border)',
  borderRadius: '12px',
  backgroundColor: 'card',
})

export const sectionTitle = css({
  margin: 0,
  fontSize: '1rem',
  lineHeight: '1.5rem',
  fontWeight: 600,
})

/**
 * Tinted header of the notes card on the feedback page: the large icon sits
 * on the right as an illustration, partly cropped, so the title stays clean.
 */
export const sectionHeader = css({
  position: 'relative',
  overflow: 'hidden',
  display: 'flex',
  alignItems: 'center',
  minHeight: '76px',
  padding: '0.875rem 1.125rem',
  paddingRight: '6.5rem',
  borderRadius: '11px 11px 0 0',
  background: 'linear-gradient(120deg, token(colors.card) 30%, #e6edff 100%)',
  borderBottom: '1px solid token(colors.border)',
  '& img': {
    position: 'absolute',
    right: '-6px',
    top: '-8px',
    userSelect: 'none',
    pointerEvents: 'none',
  },
})
