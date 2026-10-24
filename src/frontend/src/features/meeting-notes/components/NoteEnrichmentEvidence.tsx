import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import type { EnrichmentProposal } from '../api/noteEnrichmentTypes'

/** Evidence is private read-only text; it never opens chat or a published source. */
export function NoteEnrichmentEvidence({
  proposal,
}: {
  proposal: EnrichmentProposal
}) {
  const { t } = useTranslation('personalNotes')
  return (
    <div
      className={css({ display: 'grid', gap: '0.5rem', fontSize: '0.8125rem' })}
    >
      {proposal.coverage?.source_coverage === 'partial_sources' && (
        <p>{t('enrichment.partialSources')}</p>
      )}
      {proposal.evidence.length > 0 && (
        <details>
          <summary className={css({ cursor: 'pointer', paddingY: '0.375rem' })}>
            {t('enrichment.evidence')}
          </summary>
          <ol
            className={css({
              display: 'grid',
              gap: '0.75rem',
              paddingLeft: '1.25rem',
              marginTop: '0.5rem',
            })}
          >
            {proposal.evidence.map((item, index) => (
              <li key={`${item.citation.citation_id}:${index}`}>
                {item.speaker && (
                  <p className={css({ fontWeight: 600 })}>{item.speaker}</p>
                )}
                <blockquote
                  className={css({
                    whiteSpace: 'pre-wrap',
                    overflowWrap: 'anywhere',
                  })}
                >
                  {item.text}
                </blockquote>
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  )
}
