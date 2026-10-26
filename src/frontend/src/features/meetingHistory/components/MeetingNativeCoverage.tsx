import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import { MeetingNativeCaptureDomain } from './MeetingNativeCaptureDomain'
import type { MeetingNativeCoverage as NativeCoverage } from '../api/types'

/** Source metadata describes known gaps; it never establishes meeting coverage. */
export function MeetingNativeCoverage({
  coverage,
}: {
  coverage: NativeCoverage
}) {
  const { t } = useTranslation('meetingHistory')
  const hasPending = coverage.epochs.some((epoch) => epoch.state === 'pending')
  const hasFailed = coverage.epochs.some((epoch) => epoch.state === 'failed')
  const hasUnadmitted = coverage.tracks.some(
    (track) => track.captureState === 'unadmitted'
  )
  const hasUnknownLinks = coverage.tracks.some(
    (track) => track.epochRefs === undefined
  )
  const hasMissingResults = coverage.tracks.some(
    (track) => track.epochRefs?.length === 0
  )

  return (
    <section
      aria-label={t('transcript.native.coverage.title')}
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
        {t('transcript.native.coverage.title')}
      </h3>
      <p>{t('transcript.native.coverage.unverified')}</p>
      {coverage.captureDomain && (
        <MeetingNativeCaptureDomain domain={coverage.captureDomain} />
      )}
      {coverage.epochsUnavailableReason && (
        <p>
          {t(`transcript.native.coverage.${coverage.epochsUnavailableReason}`)}
        </p>
      )}
      {!coverage.observationsAvailable && (
        <p>{t('transcript.native.coverage.observationsUnavailable')}</p>
      )}
      {coverage.truncated && <p>{t('transcript.native.coverage.truncated')}</p>}
      {hasPending && <p>{t('transcript.native.coverage.pending')}</p>}
      {hasFailed && <p>{t('transcript.native.coverage.failed')}</p>}
      {hasUnadmitted && <p>{t('transcript.native.coverage.unadmitted')}</p>}
      {hasUnknownLinks && <p>{t('transcript.native.coverage.unknownLinks')}</p>}
      {hasMissingResults && (
        <p>{t('transcript.native.coverage.missingResults')}</p>
      )}
    </section>
  )
}
