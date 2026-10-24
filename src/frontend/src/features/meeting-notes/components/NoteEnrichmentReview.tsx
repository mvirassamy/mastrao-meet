import { useId, useState, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, TextArea } from '@/primitives'
import { ASSISTANT_ICON } from '@/features/meetingHistory/components/meetingContent'
import { css, cx } from '@/styled-system/css'
import type {
  EnrichmentItem,
  EnrichmentProposal,
} from '../api/noteEnrichmentTypes'
import { validEnrichmentText } from '../api/parseNoteEnrichment'
import type { NoteEnrichmentView } from '../hooks/useNoteEnrichment'
import { useFillScrollArea } from '../hooks/useFillScrollArea'
import {
  approvedItems,
  validApproval,
  type EnrichmentReviewItem,
} from '../model/NoteEnrichmentController'
import { NoteEnrichmentDecision } from './NoteEnrichmentDecision'
import { NoteEnrichmentEvidence } from './NoteEnrichmentEvidence'
import { notePageScroll } from './notePageScroll'

type DiffRow =
  | { kind: 'original'; line: number; text: string }
  | { kind: 'complement'; index: number }

/**
 * The note lines with each complement right under its anchor line, as Core
 * will insert them. Complements under one line keep their proposal order.
 */
function diffRows(
  content: string,
  items: readonly EnrichmentItem[]
): DiffRow[] {
  const lines = content.split('\n')
  const byLine = new Map<number, number[]>()
  items.forEach((item, index) => {
    byLine.set(item.after_line, [...(byLine.get(item.after_line) ?? []), index])
  })
  return lines.flatMap((text, at) => [
    { kind: 'original' as const, line: at + 1, text },
    ...(byLine.get(at + 1) ?? []).map((index) => ({
      kind: 'complement' as const,
      index,
    })),
  ])
}

/**
 * Same type, padding and height limit as the note editor, so the note does
 * not move and scrolls inside itself.
 */
const pageText = css({
  paddingX: '1.25rem',
  paddingY: '1rem',
  fontSize: '0.875rem',
  lineHeight: '1.75',
  color: 'var(--heading-foreground)',
})

const originalLine = css({
  minHeight: '1.75em',
  whiteSpace: 'pre-wrap',
  overflowWrap: 'anywhere',
})

const complementLine = css({
  display: 'flex',
  alignItems: 'flex-start',
  gap: '0.25rem',
  marginX: '-1.25rem',
  paddingRight: '0.75rem',
  backgroundColor: 'var(--success)',
  boxShadow: 'inset 3px 0 0 var(--success-foreground)',
  '&[data-kept=false]': {
    backgroundColor: 'color-mix(in srgb, var(--destructive) 7%, transparent)',
    boxShadow: 'inset 3px 0 0 var(--destructive)',
  },
})

const gutter = css({
  flex: 'none',
  width: '1.25rem',
  textAlign: 'center',
  fontFamily: 'monospace',
  fontSize: '0.75rem',
  lineHeight: '1.75rem',
  color: 'var(--success-foreground)',
  '[data-kept=false] &': { color: 'danger' },
})

const bar = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'center',
  gap: '0.375rem',
  marginX: '1.25rem',
  padding: '0.375rem 0.375rem 0.375rem 0.75rem',
  border: '1px solid token(colors.border)',
  borderRadius: '12px',
  boxShadow: '0 12px 28px -20px rgba(15, 23, 42, 0.4)',
  fontSize: '0.8125rem',
})

const secondary = css({ color: 'muted-foreground', fontSize: '0.8125rem' })

function ComplementLine({
  view,
  index,
  entry,
  inputRef,
}: {
  view: NoteEnrichmentView
  index: number
  entry: EnrichmentReviewItem
  inputRef?: RefObject<HTMLTextAreaElement>
}) {
  const { controller } = view
  const { t } = useTranslation('personalNotes')
  const idle = view.phase === 'idle'
  const number = index + 1
  return (
    <div data-kept={entry.kept} className={complementLine}>
      <span aria-hidden="true" className={gutter}>
        {entry.kept ? '+' : '−'}
      </span>
      <TextArea
        ref={inputRef}
        aria-label={t('enrichment.item', { number })}
        value={entry.text}
        rows={1}
        fieldSizing="content"
        readOnly={!idle || !entry.kept}
        aria-invalid={entry.kept && !validEnrichmentText(entry.text)}
        onChange={(event) =>
          controller.setItemText(index, event.currentTarget.value)
        }
        css={{
          flex: 1,
          minHeight: '1.75rem',
          padding: 0,
          border: 'none',
          borderRadius: 0,
          backgroundColor: 'transparent',
          resize: 'none',
          fontSize: 'inherit',
          lineHeight: 'inherit',
          color: 'inherit',
          whiteSpace: 'pre-wrap',
          '[data-kept=false] &': {
            color: 'muted-foreground',
            textDecoration: 'line-through',
          },
          _focusVisible: {
            outline: '2px solid token(colors.primary)!',
            outlineOffset: '1px!',
            borderRadius: '4px',
          },
        }}
      />
      <NoteEnrichmentDecision
        number={number}
        kept={entry.kept}
        isDisabled={!idle}
        onChange={(kept) => controller.setItemKept(index, kept)}
      />
    </div>
  )
}

/**
 * The note as it will read once saved: its own lines unchanged, each proposed
 * complement in green under the line it details, kept or refused one by one.
 * It stands in for the editor until the proposal is saved or cancelled.
 */
export function NoteEnrichmentReview({
  view,
  proposal,
  content,
  canApply,
  busy,
  firstItemRef,
  cancelRef,
  onCancel,
}: {
  view: NoteEnrichmentView
  proposal: EnrichmentProposal
  content: string
  canApply: boolean
  busy: boolean
  firstItemRef: RefObject<HTMLTextAreaElement>
  cancelRef: RefObject<HTMLButtonElement>
  onCancel: () => void
}) {
  const { controller } = view
  const { t } = useTranslation('personalNotes')
  const id = useId()
  const page = useFillScrollArea<HTMLDivElement>(true)
  const [composing, setComposing] = useState(false)
  const idle = view.phase === 'idle'
  const approved = approvedItems(view.review)
  const refused = view.review.length - approved.length
  const invalid = approved.length > 0 && !validApproval(approved)

  return (
    <section
      aria-labelledby={`${id}-title`}
      onCompositionStart={() => setComposing(true)}
      onCompositionEnd={() => setComposing(false)}
      className={css({
        display: 'grid',
        gap: '0.75rem',
        paddingBottom: '0.75rem',
      })}
    >
      <h3 id={`${id}-title`} className={css({ srOnly: true })}>
        {t('enrichment.review')}
      </h3>
      <div
        ref={page.ref}
        className={cx(pageText, notePageScroll)}
        style={{ height: page.height }}
      >
        {diffRows(content, proposal.items).map((row) =>
          row.kind === 'original' ? (
            <p key={`line-${row.line}`} className={originalLine}>
              {row.text}
            </p>
          ) : (
            <ComplementLine
              key={`item-${row.index}`}
              view={view}
              index={row.index}
              entry={view.review[row.index]}
              inputRef={row.index === 0 ? firstItemRef : undefined}
            />
          )
        )}
      </div>
      <div className={bar}>
        <img src={ASSISTANT_ICON} alt="" width={18} height={18} />
        <span className={css({ color: 'muted-foreground' })}>
          {refused === 0
            ? t('enrichment.proposedCount', { count: approved.length })
            : t('enrichment.reviewCount', {
                kept: approved.length,
                refused,
              })}
        </span>
        <span className={css({ flex: 1 })} />
        {view.modified && (
          <Button
            variant="ghost"
            size="sm"
            isDisabled={!idle}
            onPress={controller.resetReview}
          >
            {t('enrichment.revert')}
          </Button>
        )}
        <Button
          ref={cancelRef}
          variant="ghost"
          size="sm"
          isDisabled={!controller.canDiscard()}
          onPress={onCancel}
        >
          {t('enrichment.cancel')}
        </Button>
        <Button
          size="sm"
          isDisabled={
            !idle ||
            view.stale ||
            invalid ||
            approved.length === 0 ||
            composing ||
            !canApply
          }
          loading={busy}
          onPress={() => void controller.apply()}
        >
          {t('enrichment.apply')}
        </Button>
      </div>
      {invalid && (
        <p
          role="alert"
          className={cx(
            secondary,
            css({ marginX: '1.25rem', color: 'danger' })
          )}
        >
          {t('enrichment.invalidAdditions')}
        </p>
      )}
      {view.modified && (
        <p className={cx(secondary, css({ marginX: '1.25rem' }))}>
          {t('enrichment.modified')}
        </p>
      )}
      <div className={css({ marginX: '1.25rem' })}>
        <NoteEnrichmentEvidence proposal={proposal} />
      </div>
    </section>
  )
}
