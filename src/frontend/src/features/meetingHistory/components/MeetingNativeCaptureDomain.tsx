import { useTranslation } from 'react-i18next'
import {
  nativeDomainBlockerReasons,
  type MeetingNativeCaptureDomain as Domain,
} from '../api/meetingNativeCaptureDomain'

/** Closure applies to this known inventory; the parent retains unverified coverage. */
export function MeetingNativeCaptureDomain({ domain }: { domain: Domain }) {
  const { t } = useTranslation('meetingHistory')
  const counts = domain.counts
  const failures =
    counts &&
    (counts.failed_captures > 0 ||
      counts.aborted_captures > 0 ||
      counts.limit_reached_captures > 0)
  return (
    <div role="group" aria-label={t('transcript.native.domain.title')}>
      <p>{t(`transcript.native.domain.state.${domain.closureState}`)}</p>
      {domain.reason && (
        <p>{t(`transcript.native.domain.reason.${domain.reason}`)}</p>
      )}
      {counts && (
        <p>
          {t('transcript.native.domain.counts', {
            epochs: counts.epochs,
            terminal: counts.terminal_captures,
            captures: counts.captures,
            sources: counts.verified_sources,
            results: counts.verified_results,
          })}
        </p>
      )}
      {failures && (
        <p>
          {t('transcript.native.domain.failures', {
            failed: counts.failed_captures,
            aborted: counts.aborted_captures,
            limited: counts.limit_reached_captures,
          })}
        </p>
      )}
      {nativeDomainBlockerReasons
        .filter((reason) =>
          domain.blockers.some((blocker) => blocker.reason === reason)
        )
        .map((reason) => (
          <p key={reason}>{t(`transcript.native.domain.blocker.${reason}`)}</p>
        ))}
    </div>
  )
}
