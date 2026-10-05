import { css, cva } from '@/styled-system/css'

/*
 * Layout of the meeting detail, shared by the page and its loading
 * skeleton so both have the same shape.
 */

export const detailHeader = css({ marginBottom: '1.5rem' })

/** Summary and transcript side by side on wide screens. */
export const detailSections = css({
  display: 'grid',
  gridTemplateColumns: {
    base: 'minmax(0, 1fr)',
    lg: 'minmax(0, 1fr) minmax(0, 1.2fr)',
  },
  alignItems: 'start',
  gap: { base: '1rem', md: '1.25rem' },
})

export const sectionCard = css({
  minWidth: 0,
  border: '1px solid token(colors.border)',
  borderRadius: '12px',
  backgroundColor: 'card',
})

export const sectionBody = css({ padding: '1rem 1.125rem 1.25rem' })

export const sectionTitle = css({
  margin: 0,
  fontSize: '1rem',
  lineHeight: '1.5rem',
  fontWeight: 600,
})

const greyHeader = {
  background: 'linear-gradient(120deg, token(colors.card) 30%, #eef0f4 100%)',
  '& img': { filter: 'grayscale(1)', opacity: 0.45 },
}

/**
 * Tinted header: the large section icon sits on the right as an
 * illustration, partly cropped, so the title stays clean. Grey while the
 * content is not ready or when there is nothing to show, pink when the
 * processing failed.
 */
export const sectionHeader = cva({
  base: {
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
  },
  variants: {
    look: {
      content: {},
      pending: greyHeader,
      empty: greyHeader,
      failed: {
        background:
          'linear-gradient(120deg, token(colors.card) 30%, #fcebec 100%)',
      },
    },
  },
})
