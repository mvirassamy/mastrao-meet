import { useEffect, useId, useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/primitives'
import { ASSISTANT_ICON } from '@/features/meetingHistory/components/meetingContent'
import { css } from '@/styled-system/css'
import {
  useNoteEnrichment,
  type NoteEnrichmentView,
} from '../hooks/useNoteEnrichment'
import {
  usePersonalNotes,
  type PersonalNotesContextValue,
} from '../hooks/usePersonalNotes'
import type { EnrichmentViewState } from '../model/NoteEnrichmentController'
import { NoteEnrichmentReview } from './NoteEnrichmentReview'
import { NoteEnrichmentToast } from './NoteEnrichmentToast'

/**
 * Only history supplies a meeting reference. Guest notes have no AI controls.
 * `children` is the note editor; a proposal under review stands in for it.
 */
export function NoteEnrichment({
  meetingRef,
  children,
}: {
  meetingRef: string
  children: ReactNode
}) {
  const notes = usePersonalNotes()
  if (!notes || notes.isGuest || !notes.hasLoaded) return children
  return (
    <EnrichmentPanel notes={notes} meetingRef={meetingRef}>
      {children}
    </EnrichmentPanel>
  )
}

const invite = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'center',
  gap: '0.75rem',
  padding: '0.75rem 0.875rem',
  border: '1px solid token(colors.border)',
  borderRadius: '12px',
})

const secondary = css({ color: 'muted-foreground', fontSize: '0.8125rem' })

const panel = css({
  display: 'grid',
  gap: '0.75rem',
  margin: '1.25rem',
  paddingTop: '1rem',
  borderTop: '1px solid token(colors.border)',
  fontSize: '0.875rem',
})

/** An operation in progress: shown as a loader, announced as text. */
function progressLabel(view: EnrichmentViewState): string | null {
  switch (view.phase) {
    case 'loading':
      return 'loading'
    case 'generating':
      return 'generating'
    case 'discarding':
      return 'discarding'
    case 'applying':
      return 'applying'
    case 'undoing':
      return 'undoing'
    case 'reconciling':
      return 'verifying'
    case 'idle':
      break
  }
  if (view.dismissed) return null
  return view.server?.proposal?.status === 'generating' ? 'generating' : null
}

/** A settled outcome worth reading once the operation is over. */
function outcomeLabel(view: EnrichmentViewState): string | null {
  if (view.phase !== 'idle') return null
  if (view.notice) return view.notice
  if (view.dismissed) return null
  switch (view.server?.proposal?.status) {
    case 'empty':
      return 'empty'
    case 'failed':
      return 'failed'
    default:
      return null
  }
}

function EnrichmentPanel({
  notes,
  meetingRef,
  children,
}: {
  notes: PersonalNotesContextValue
  meetingRef: string
  children: ReactNode
}) {
  const view = useNoteEnrichment(notes, meetingRef)
  const { controller } = view
  const { t } = useTranslation('personalNotes')
  const id = useId()
  const generateRef = useRef<HTMLButtonElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)
  const firstItemRef = useRef<HTMLTextAreaElement>(null)
  const lastPreview = useRef<string | null>(null)
  const returnFocus = useRef(false)
  const proposal = view.proposal
  // A proposal made for another version of the note cannot be placed in it.
  const reviewing = Boolean(
    proposal?.status === 'ready' && !view.stale && !view.dismissed
  )
  const idle = view.phase === 'idle'
  const hasLastAddition = Boolean(
    view.server?.last_applied && view.server.receipt?.action !== 'undo'
  )
  const generating =
    view.phase === 'generating' ||
    (!view.dismissed && view.server?.proposal?.status === 'generating')
  const canGenerate = Boolean(
    view.server?.eligible && idle && !generating && !notes.hasUnsavedChanges
  )
  const progress = progressLabel(view)
  const outcome = outcomeLabel(view)
  // The block's main button carries the loader while anything is in progress.
  const busy = Boolean(progress) && !view.canRetryMutation

  useEffect(() => {
    if (!reviewing || !proposal || lastPreview.current === proposal.id) return
    lastPreview.current = proposal.id
    // Focus follows only a request made from the enrichment controls.
    if (
      document.activeElement === generateRef.current ||
      document.activeElement === cancelRef.current ||
      document.activeElement === document.body
    )
      firstItemRef.current?.focus()
  }, [reviewing, proposal])

  useEffect(() => {
    if (!returnFocus.current || view.phase !== 'idle') return
    returnFocus.current = false
    if (
      document.activeElement === document.body ||
      document.activeElement === cancelRef.current
    )
      generateRef.current?.focus()
  }, [view.phase, view.notice])

  if (view.unavailable) return children

  const cancel = async () => {
    returnFocus.current = true
    await controller.discard()
  }

  return (
    <>
      {reviewing && proposal ? (
        <NoteEnrichmentReview
          key={proposal.id}
          view={view}
          proposal={proposal}
          content={notes.content}
          canApply={!notes.hasUnsavedChanges}
          busy={busy}
          firstItemRef={firstItemRef}
          cancelRef={cancelRef}
          onCancel={() => void cancel()}
        />
      ) : (
        children
      )}
      {/* During review, the bar under the note holds the actions. */}
      <section
        aria-label={t('enrichment.title')}
        className={reviewing ? undefined : panel}
      >
        {!reviewing && (
          <div className={invite}>
            <img src={ASSISTANT_ICON} alt="" width={36} height={36} />
            <div className={css({ flex: 1, minWidth: '12rem' })}>
              <p className={css({ fontWeight: 600 })}>
                {t('enrichment.title')}
              </p>
              <p id={`${id}-help`} className={secondary}>
                {t('enrichment.help')}
              </p>
            </div>
            <div className={css({ display: 'flex', gap: '0.5rem' })}>
              {hasLastAddition && (
                <Button
                  variant="ghost"
                  size="sm"
                  isDisabled={!idle || !controller.canUndo()}
                  onPress={() => void controller.undo()}
                >
                  {t('enrichment.undo')}
                </Button>
              )}
              <Button
                ref={generateRef}
                size="sm"
                aria-describedby={`${id}-help`}
                isDisabled={!canGenerate}
                loading={busy}
                onPress={() => void controller.generate()}
              >
                {t('enrichment.generate')}
              </Button>
            </div>
          </div>
        )}
        {view.server?.reason && !view.server.eligible && !generating && (
          <p className={secondary}>
            {t(`enrichment.reasons.${view.server.reason}`)}
          </p>
        )}
        {hasLastAddition &&
          !controller.canUndo() &&
          !notes.isMutationLocked && (
            <p className={secondary}>{t('enrichment.undoUnavailable')}</p>
          )}
        <p
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className={progress ? css({ srOnly: true }) : secondary}
        >
          {progress && t(`enrichment.${progress}`)}
          {!progress && outcome && t(`enrichment.${outcome}`)}
        </p>
        {generating && controller.canDiscard() && (
          <div>
            <Button
              ref={cancelRef}
              variant="outline"
              size="sm"
              onPress={() => void cancel()}
            >
              {t('enrichment.cancel')}
            </Button>
          </div>
        )}
        <EnrichmentProblemToast view={view} retryCancel={() => void cancel()} />
      </section>
    </>
  )
}

/** At most one toast, for the most pressing problem, with its single fix. */
function EnrichmentProblemToast({
  view,
  retryCancel,
}: {
  view: NoteEnrichmentView
  retryCancel: () => void
}) {
  const { t } = useTranslation('personalNotes')
  const { controller } = view
  const retry = t('enrichment.retry')
  if (view.canRetryMutation)
    return (
      <NoteEnrichmentToast
        tone="error"
        message={t('enrichment.retryMutationTitle')}
        action={{
          label: retry,
          onPress: () => void controller.retryMutation(),
        }}
      />
    )
  if (view.error === 'discard')
    return (
      <NoteEnrichmentToast
        tone="error"
        message={t('enrichment.errors.discard')}
        action={{
          label: retry,
          isDisabled: !controller.canDiscard(),
          onPress: retryCancel,
        }}
      />
    )
  if (view.error)
    return (
      <NoteEnrichmentToast
        tone="error"
        message={t(`enrichment.errors.${view.error}`)}
        action={{ label: retry, onPress: () => void controller.refresh() }}
      />
    )
  if (
    view.phase === 'idle' &&
    !view.dismissed &&
    view.server?.proposal?.status === 'unknown'
  )
    return (
      <NoteEnrichmentToast
        tone="warning"
        message={t('enrichment.unknown')}
        action={{ label: retry, onPress: () => void controller.refresh() }}
      />
    )
  if (view.stale && !view.dismissed)
    return (
      <NoteEnrichmentToast tone="warning" message={t('enrichment.stale')} />
    )
  return null
}
