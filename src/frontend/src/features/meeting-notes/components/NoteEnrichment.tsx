import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, TextArea } from '@/primitives'
import { css } from '@/styled-system/css'
import { MAX_ENRICHMENT_LENGTH } from '../api/noteEnrichmentTypes'
import { validEnrichmentText } from '../api/parseNoteEnrichment'
import { useNoteEnrichment } from '../hooks/useNoteEnrichment'
import {
  usePersonalNotes,
  type PersonalNotesContextValue,
} from '../hooks/usePersonalNotes'
import type { EnrichmentViewState } from '../model/NoteEnrichmentController'
import { NoteEnrichmentEvidence } from './NoteEnrichmentEvidence'

/** Only history supplies a meeting reference. Guest notes have no AI controls. */
export function NoteEnrichment({ meetingRef }: { meetingRef: string }) {
  const notes = usePersonalNotes()
  if (!notes || notes.isGuest || !notes.hasLoaded) return null
  return <EnrichmentPanel notes={notes} meetingRef={meetingRef} />
}

function operationLabel(view: EnrichmentViewState): string | null {
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
  switch (view.server?.proposal?.status) {
    case 'generating':
      return 'generating'
    case 'empty':
      return 'empty'
    case 'failed':
      return 'failed'
    case 'unknown':
      return 'unknown'
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
  const { controller } = view
  const { t } = useTranslation('personalNotes')
  const id = useId()
  const generateRef = useRef<HTMLButtonElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)
  const previewRef = useRef<HTMLTextAreaElement>(null)
  const lastPreview = useRef<string | null>(null)
  const returnFocus = useRef(false)
  const [composing, setComposing] = useState(false)
  const proposal = view.proposal
  const showPreview = Boolean(
    proposal &&
    (proposal.status === 'ready' || proposal.status === 'stale') &&
    !view.dismissed
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
  const validAdditions =
    validEnrichmentText(view.additions) && view.additions.trim().length > 0
  const label = operationLabel(view)
  const cancelLabel = t(
    view.error === 'discard' ? 'enrichment.retryCancel' : 'enrichment.cancel'
  )

  useEffect(() => {
    if (!showPreview || !proposal || lastPreview.current === proposal.id) return
    lastPreview.current = proposal.id
    // A completed generation must not steal focus from ongoing note-taking.
    if (
      document.activeElement === generateRef.current ||
      document.activeElement === cancelRef.current ||
      document.activeElement === document.body
    )
      previewRef.current?.focus()
  }, [showPreview, proposal])

  useEffect(() => {
    if (!returnFocus.current || view.phase !== 'idle') return
    returnFocus.current = false
    if (
      document.activeElement === document.body ||
      document.activeElement === cancelRef.current
    )
      generateRef.current?.focus()
  }, [view.phase, view.notice])

  if (view.unavailable) return null

  const cancel = async () => {
    returnFocus.current = true
    await controller.discard()
  }

  return (
    <section
      aria-label={t('enrichment.title')}
      className={css({
        display: 'grid',
        gap: '0.75rem',
        margin: '1.25rem',
        paddingTop: '1rem',
        borderTop: '1px solid token(colors.border)',
        fontSize: '0.875rem',
      })}
    >
      <div
        className={css({
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: '0.5rem',
        })}
      >
        <Button
          ref={generateRef}
          variant="outline"
          size="sm"
          aria-describedby={`${id}-help`}
          isDisabled={!canGenerate}
          onPress={() => void controller.generate()}
        >
          {t('enrichment.generate')}
        </Button>
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
      </div>
      <p id={`${id}-help`} className={css({ color: 'muted-foreground' })}>
        {t('enrichment.help')}
      </p>
      {view.server?.reason && !view.server.eligible && !generating && (
        <p>{t(`enrichment.reasons.${view.server.reason}`)}</p>
      )}
      {hasLastAddition && !controller.canUndo() && !notes.isMutationLocked && (
        <p>{t('enrichment.undoUnavailable')}</p>
      )}
      <p role="status" aria-live="polite" aria-atomic="true">
        {label && t(`enrichment.${label}`)}
        {!label && view.notice && t(`enrichment.${view.notice}`)}
      </p>
      {view.error && <p role="alert">{t(`enrichment.errors.${view.error}`)}</p>}
      {view.canRetryMutation && (
        <div>
          <p id={`${id}-retry-help`}>{t('enrichment.retryMutationHelp')}</p>
          <Button
            variant="outline"
            size="sm"
            aria-describedby={`${id}-retry-help`}
            onPress={() => void controller.retryMutation()}
          >
            {t('enrichment.retryMutation')}
          </Button>
        </div>
      )}
      {view.stale && !view.dismissed && (
        <p role="alert">{t('enrichment.stale')}</p>
      )}
      {(view.error ||
        view.phase === 'reconciling' ||
        view.cancellationPending ||
        proposal?.status === 'unknown') && (
        <Button
          variant="outline"
          size="sm"
          isDisabled={
            !idle && view.phase !== 'reconciling' && !view.cancellationPending
          }
          onPress={() => void controller.refresh()}
        >
          {t('enrichment.check')}
        </Button>
      )}
      {showPreview && proposal && (
        <div
          className={css({
            display: 'grid',
            gap: '0.75rem',
            padding: '1rem',
            border: '1px solid token(colors.border)',
            borderRadius: '12px',
            backgroundColor: 'muted',
          })}
        >
          <label htmlFor={`${id}-preview`} className={css({ fontWeight: 600 })}>
            {t('enrichment.preview')}
          </label>
          <TextArea
            ref={previewRef}
            id={`${id}-preview`}
            value={view.additions}
            readOnly={!idle}
            aria-describedby={`${id}-review ${id}-length`}
            aria-invalid={!validAdditions}
            onChange={(event) =>
              controller.setAdditions(event.currentTarget.value)
            }
            onCompositionStart={() => setComposing(true)}
            onCompositionEnd={() => setComposing(false)}
            css={{
              minHeight: '10rem',
              padding: '0.75rem',
              resize: 'vertical',
              backgroundColor: 'background',
              whiteSpace: 'pre-wrap',
              lineHeight: '1.75',
            }}
          />
          <p id={`${id}-review`}>{t('enrichment.review')}</p>
          <p id={`${id}-length`}>
            {t('enrichment.length', {
              count: view.additions.length,
              maximum: MAX_ENRICHMENT_LENGTH,
            })}
          </p>
          {!validAdditions && (
            <p role="alert">{t('enrichment.invalidAdditions')}</p>
          )}
          {view.modified && <p>{t('enrichment.modified')}</p>}
          <NoteEnrichmentEvidence proposal={proposal} />
          <div
            className={css({
              display: 'flex',
              flexWrap: 'wrap',
              gap: '0.5rem',
            })}
          >
            <Button
              size="sm"
              isDisabled={
                !idle ||
                view.stale ||
                !validAdditions ||
                composing ||
                notes.hasUnsavedChanges
              }
              onPress={() => void controller.apply()}
            >
              {t('enrichment.apply')}
            </Button>
            <Button
              ref={cancelRef}
              variant="outline"
              size="sm"
              isDisabled={!controller.canDiscard()}
              onPress={() => void cancel()}
            >
              {cancelLabel}
            </Button>
          </div>
        </div>
      )}
      {generating && controller.canDiscard() && (
        <Button
          ref={cancelRef}
          variant="outline"
          size="sm"
          onPress={() => void cancel()}
        >
          {cancelLabel}
        </Button>
      )}
    </section>
  )
}
