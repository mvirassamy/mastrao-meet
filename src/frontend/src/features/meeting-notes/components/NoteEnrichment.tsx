import { useEffect, useRef, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/primitives'
import { ASSISTANT_ICON } from '@/features/meetingHistory/components/meetingContent'
import { css } from '@/styled-system/css'
import {
  useNoteEnrichment,
  type NoteEnrichmentView,
} from '../hooks/useNoteEnrichment'
import { useNoteHistory } from '../hooks/useNoteHistory'
import {
  usePersonalNotes,
  type PersonalNotesContextValue,
} from '../hooks/usePersonalNotes'
import type { EnrichmentViewState } from '../model/NoteEnrichmentController'
import { NoteEnrichmentReview } from './NoteEnrichmentReview'
import { NoteEnrichmentToast } from './NoteEnrichmentToast'
import { NoteSaveStatus } from './NoteSaveStatus'
import { NotesToolbar } from './NotesToolbar'
import { PersonalNotesEditor } from './PersonalNotesEditor'

/**
 * The meeting page note: a toolbar above the editor, and a proposal under
 * review standing in for it. Guest notes have no AI controls, only the editor.
 */
export function NoteEnrichment({ meetingRef }: { meetingRef: string }) {
  const notes = usePersonalNotes()
  if (!notes || notes.isGuest || !notes.hasLoaded)
    return <PersonalNotesEditor fit="content" />
  return <EnrichmentPanel notes={notes} meetingRef={meetingRef} />
}

const secondary = css({ color: 'muted-foreground', fontSize: '0.8125rem' })

const panel = css({
  display: 'grid',
  gap: '0.5rem',
  marginX: '1.25rem',
  marginBottom: '0.75rem',
})

/** ⌘Z / ⇧⌘Z (Ctrl on other systems) follow the note history, not the field's. */
function historyKey(event: KeyboardEvent): 'undo' | 'redo' | null {
  if (event.nativeEvent.isComposing) return null
  if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'z')
    return null
  return event.shiftKey ? 'redo' : 'undo'
}

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
    default:
      return null
  }
}

function EnrichmentPanel({
  notes,
  meetingRef,
}: {
  notes: PersonalNotesContextValue
  meetingRef: string
}) {
  const view = useNoteEnrichment(notes, meetingRef)
  const history = useNoteHistory(notes)
  const { controller } = view
  const { t } = useTranslation('personalNotes')
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

  if (view.unavailable) return <PersonalNotesEditor fit="content" />

  const cancel = async () => {
    returnFocus.current = true
    await controller.discard()
  }

  const onEditorKeyDown = (event: KeyboardEvent) => {
    const action = historyKey(event)
    if (!action) return
    event.preventDefault()
    if (action === 'undo' && history.canUndo) history.undo()
    if (action === 'redo' && history.canRedo) history.redo()
  }

  return (
    <>
      {!reviewing && (
        <NotesToolbar
          status={<NoteSaveStatus notes={notes} />}
          canUndo={history.canUndo}
          canRedo={history.canRedo}
          onUndo={history.undo}
          onRedo={history.redo}
        >
          <Button
            ref={generateRef}
            variant="secondary"
            size="sm"
            icon={<img src={ASSISTANT_ICON} alt="" width={16} height={16} />}
            tooltip={t('enrichment.help')}
            isDisabled={!canGenerate}
            loading={busy}
            onPress={() => void controller.generate()}
          >
            {t('enrichment.generate')}
          </Button>
          {generating && controller.canDiscard() && (
            <Button
              ref={cancelRef}
              variant="ghost"
              size="sm"
              onPress={() => void cancel()}
            >
              {t('enrichment.cancel')}
            </Button>
          )}
        </NotesToolbar>
      )}
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
        <div onKeyDownCapture={onEditorKeyDown}>
          <PersonalNotesEditor fit="content" showSaveStatus={false} />
        </div>
      )}
      {/* During review, the bar under the note holds the actions. */}
      <section aria-label={t('enrichment.title')} className={panel}>
        {view.server?.reason && !view.server.eligible && !generating && (
          <p className={secondary}>
            {t(`enrichment.reasons.${view.server.reason}`)}
          </p>
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
        <EnrichmentProblemToast
          view={view}
          canGenerate={canGenerate}
          retryCancel={() => void cancel()}
        />
      </section>
    </>
  )
}

/** At most one toast, for the most pressing problem, with its single fix. */
function EnrichmentProblemToast({
  view,
  canGenerate,
  retryCancel,
}: {
  view: NoteEnrichmentView
  canGenerate: boolean
  retryCancel: () => void
}) {
  const { t } = useTranslation('personalNotes')
  const { controller } = view
  const retry = t('enrichment.retry')
  // Generation reads the current state first and sends a new versioned command.
  const regenerate = {
    label: retry,
    isDisabled: !canGenerate,
    onPress: () => void controller.generate(),
  }
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
  if (view.error === 'generate')
    return (
      <NoteEnrichmentToast
        tone="error"
        message={t('enrichment.errors.generate')}
        action={regenerate}
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
    view.server?.proposal?.status === 'failed'
  )
    return (
      <NoteEnrichmentToast
        tone="error"
        message={t('enrichment.failed')}
        action={regenerate}
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
      <NoteEnrichmentToast
        tone="warning"
        message={t('enrichment.stale')}
        action={regenerate}
      />
    )
  return null
}
