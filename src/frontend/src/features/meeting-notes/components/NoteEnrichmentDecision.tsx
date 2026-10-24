import { useTranslation } from 'react-i18next'
import { CheckIcon, CloseIcon } from '@/icons'
import { Button } from '@/primitives'
import { css, cx } from '@/styled-system/css'

/** Two round buttons on small screens; a « Garder | Refuser » pill from md. */
const group = css({
  display: 'flex',
  flex: 'none',
  gap: '0.375rem',
  md: {
    gap: '2px',
    padding: '2px',
    border: '1px solid token(colors.border)',
    borderRadius: 'full',
    backgroundColor: 'background',
  },
})

const choice = css({
  width: '28px!',
  height: '28px!',
  minHeight: '28px!',
  paddingX: '0!',
  justifyContent: 'center',
  borderRadius: 'full!',
  border: '1.5px solid',
  backgroundColor: 'background!',
  fontSize: '0.75rem!',
  fontWeight: 600,
  '&[aria-pressed=true]': { color: 'white!' },
  md: {
    width: 'auto!',
    height: '24px!',
    minHeight: '24px!',
    paddingX: '0.5625rem!',
    border: 'none',
    color: 'muted-foreground!',
  },
})

const keep = css({
  color: 'var(--success-foreground)!',
  borderColor: 'color-mix(in srgb, var(--success-foreground) 35%, transparent)',
  '&[aria-pressed=true]': {
    backgroundColor: 'var(--success-foreground)!',
    borderColor: 'var(--success-foreground)',
  },
})

const refuse = css({
  color: 'danger!',
  borderColor: 'color-mix(in srgb, var(--destructive) 35%, transparent)',
  '&[aria-pressed=true]': {
    backgroundColor: 'danger!',
    borderColor: 'danger',
  },
})

/** Only the pill has room for the words; the round buttons keep the icon. */
const word = css({ display: 'none', md: { display: 'inline' } })

/** Keep or refuse one proposed complement. */
export function NoteEnrichmentDecision({
  number,
  kept,
  isDisabled,
  onChange,
}: {
  number: number
  kept: boolean
  isDisabled: boolean
  onChange: (kept: boolean) => void
}) {
  const { t } = useTranslation('personalNotes')
  return (
    <div className={group}>
      <Button
        variant="ghost"
        size="xs"
        aria-label={t('enrichment.keep', { number })}
        aria-pressed={kept}
        isDisabled={isDisabled}
        className={cx(choice, keep)}
        onPress={() => onChange(true)}
      >
        <CheckIcon aria-hidden="true" />
        <span className={word}>{t('enrichment.keepShort')}</span>
      </Button>
      <Button
        variant="ghost"
        size="xs"
        aria-label={t('enrichment.refuse', { number })}
        aria-pressed={!kept}
        isDisabled={isDisabled}
        className={cx(choice, refuse)}
        onPress={() => onChange(false)}
      >
        <CloseIcon aria-hidden="true" />
        <span className={word}>{t('enrichment.refuseShort')}</span>
      </Button>
    </div>
  )
}
