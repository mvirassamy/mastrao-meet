import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import type {
  LiveDomainBlockerReason,
  MeetingLiveObservedDomain as LiveDomain,
} from '../api/meetingLiveObservedDomain'

function blockerMessage(reason: LiveDomainBlockerReason) {
  switch (reason) {
    case 'identity_conflict':
    case 'correlation_conflict':
    case 'inventory_contradiction':
      return 'contradiction'
    case 'correlation_unresolved':
    case 'live_admission_absent':
    case 'core_source_unrepresented':
      return 'admission'
    case 'publication_unobserved':
    case 'epoch_terminal_unobserved':
      return 'microphone'
    case 'journal_end_unconfirmed':
      return 'journal'
    case 'send_stop_unacknowledged':
      return 'transmission'
    default:
      return 'limits'
  }
}

/** Known microphone stages do not establish full meeting or audio coverage. */
export function MeetingLiveObservedDomain({ domain }: { domain: LiveDomain }) {
  const { t } = useTranslation('meetingHistory')
  const messages = [
    ...new Set(domain.blockers.map(({ reason }) => blockerMessage(reason))),
  ]
  return (
    <section
      aria-label={t('transcript.live.domain.title')}
      className={css({
        padding: '0.75rem',
        marginBottom: '1rem',
        border: '1px solid token(colors.border)',
        borderRadius: '8px',
        fontSize: '0.8125rem',
        lineHeight: '1.25rem',
        '& p': { margin: 0, marginTop: '0.375rem' },
      })}
    >
      <h3 className={css({ margin: 0, fontSize: 'inherit', fontWeight: 600 })}>
        {t('transcript.live.domain.title')}
      </h3>
      <p>{t('transcript.live.domain.unverified')}</p>
      {domain.counts ? (
        <>
          <p>{t('transcript.live.domain.microphones', domain.counts)}</p>
          <p>{t('transcript.live.domain.sources', domain.counts)}</p>
          <p>{t('transcript.live.domain.stages', domain.counts)}</p>
        </>
      ) : (
        <p>{t('transcript.live.domain.unknown')}</p>
      )}
      {messages.length > 0 && (
        <ul>
          {messages.map((message) => (
            <li key={message}>
              {t(`transcript.live.domain.blockers.${message}`)}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
