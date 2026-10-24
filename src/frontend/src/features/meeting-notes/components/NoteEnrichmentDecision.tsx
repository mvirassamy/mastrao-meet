import { useTranslation } from 'react-i18next'
import { CheckIcon, CloseIcon } from '@/icons'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'

/** On small screens only the icons fit; the words come back from md. */
const word = css({ display: 'none', md: { display: 'inline' } })

/**
 * Keep or refuse one proposed complement: two joined platform buttons, the
 * chosen one filled (default to keep, destructive to refuse).
 */
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
    <div className={css({ display: 'flex', flex: 'none' })}>
      <Button
        variant={kept ? 'default' : 'outline'}
        size="xs"
        groupPosition="left"
        aria-label={t('enrichment.keep', { number })}
        aria-pressed={kept}
        isDisabled={isDisabled}
        icon={<CheckIcon aria-hidden="true" />}
        onPress={() => onChange(true)}
      >
        <span className={word}>{t('enrichment.keepShort')}</span>
      </Button>
      <Button
        variant={kept ? 'outline' : 'destructive'}
        size="xs"
        groupPosition="right"
        aria-label={t('enrichment.refuse', { number })}
        aria-pressed={!kept}
        isDisabled={isDisabled}
        icon={<CloseIcon aria-hidden="true" />}
        onPress={() => onChange(false)}
      >
        <span className={word}>{t('enrichment.refuseShort')}</span>
      </Button>
    </div>
  )
}
