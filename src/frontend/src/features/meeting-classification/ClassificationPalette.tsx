import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react'
import { useTranslation } from 'react-i18next'
import { AddIcon, SearchIcon, WarningIcon } from '@/icons'
import { Button } from '@/primitives'
import { css, cx } from '@/styled-system/css'
import type {
  DestinationSearch,
  MatterDestination,
} from './ClassificationDialog'
import { DOSSIER_ICON } from './ClassificationStatus'
import { Kbd, PaletteFooter } from './ClassificationSteps'

/** Wait for a pause in typing before asking the server for folders. */
const SEARCH_DELAY_MS = 250

type PaletteOption =
  | { kind: 'matter'; matter: MatterDestination }
  | { kind: 'more' }
  | { kind: 'create' }

const optionKey = (option: PaletteOption) =>
  option.kind === 'matter' ? option.matter.matter_ref : option.kind

/**
 * Search field driving a list of folders: arrows move through the rows
 * while focus stays in the field, Enter picks the active row.
 */
export function ClassificationPalette({
  search,
  onSearch,
  onLoadMore,
  onChoose,
  onCreate,
  onClose,
}: {
  search: DestinationSearch
  onSearch: (query: string) => void
  onLoadMore: () => void
  onChoose: (matter: MatterDestination) => void
  onCreate: (label: string) => void
  onClose: () => void
}) {
  const { t } = useTranslation('meetingHistory')
  const listId = useId()
  // The field shows the search the list answers, also after a step back.
  const [query, setQuery] = useState(search.query)
  const [active, setActive] = useState(0)
  const searchTimer = useRef<number | undefined>(undefined)
  const listRef = useRef<HTMLUListElement>(null)
  const name = query.trim()

  useEffect(() => () => window.clearTimeout(searchTimer.current), [])

  const options: PaletteOption[] = [
    ...search.items.map((matter) => ({ kind: 'matter' as const, matter })),
    ...(search.hasMore ? [{ kind: 'more' as const }] : []),
    { kind: 'create' },
  ]
  const activeIndex = Math.min(active, options.length - 1)
  const activeOption = options[activeIndex]
  const optionId = (index: number) => `${listId}-${optionKey(options[index])}`

  useEffect(() => {
    listRef.current
      ?.querySelector('[aria-selected="true"]')
      ?.scrollIntoView?.({ block: 'nearest' })
  }, [activeIndex])

  function changeQuery(value: string) {
    setQuery(value)
    setActive(0)
    window.clearTimeout(searchTimer.current)
    searchTimer.current = window.setTimeout(
      () => onSearch(value.trim()),
      SEARCH_DELAY_MS
    )
  }

  function select(option: PaletteOption) {
    switch (option.kind) {
      case 'matter':
        return onChoose(option.matter)
      case 'more':
        return onLoadMore()
      case 'create':
        return onCreate(name)
    }
  }

  function moveWithKeys(event: KeyboardEvent<HTMLInputElement>) {
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault()
        setActive((activeIndex + 1) % options.length)
        return
      case 'ArrowUp':
        event.preventDefault()
        setActive((activeIndex - 1 + options.length) % options.length)
        return
      case 'Enter':
        event.preventDefault()
        // The list still answers an older search: search now, choose next.
        if (name !== search.query) {
          window.clearTimeout(searchTimer.current)
          onSearch(name)
          return
        }
        // A folder of that name may still be loading: never create on Enter
        // then. Listed folders (load more, refresh) stay selectable.
        if (search.phase === 'loading' && activeOption.kind === 'create') return
        select(activeOption)
    }
  }

  return (
    <>
      <label
        className={css({
          display: 'flex',
          alignItems: 'center',
          gap: '0.625rem',
          paddingX: '1rem',
          borderBottom: '1px solid token(colors.border)',
          color: 'muted-foreground',
        })}
      >
        <SearchIcon size={18} aria-hidden="true" />
        <input
          type="text"
          role="combobox"
          aria-label={t('classification.search')}
          aria-expanded="true"
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={optionId(activeIndex)}
          placeholder={t('classification.placeholder')}
          value={query}
          maxLength={100}
          // A palette opens on its search field so typing filters at once.
          // eslint-disable-next-line jsx-a11y/no-autofocus
          autoFocus
          autoComplete="off"
          onChange={(event) => changeQuery(event.target.value)}
          onKeyDown={moveWithKeys}
          className={css({
            flex: 1,
            minWidth: 0,
            height: '3.25rem',
            border: 'none',
            outline: 'none',
            // The caret shows focus; the field is the only control here.
            _focusVisible: { outline: 'none!' },
            background: 'none',
            color: 'var(--heading-foreground)',
            fontSize: '0.9375rem',
            _placeholder: { color: 'muted-foreground' },
          })}
        />
      </label>
      <div
        className={css({
          maxHeight: '22rem',
          overflowY: 'auto',
          padding: '0.375rem',
        })}
      >
        <SearchState
          search={search}
          query={name}
          onRetry={() => onSearch(name)}
        />
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          aria-label={t('classification.destinations')}
          className={css({ margin: 0, padding: 0, listStyle: 'none' })}
        >
          {options.map((option, index) => (
            // Keyboard selection goes through the search field, which owns
            // the active option (aria-activedescendant).
            // eslint-disable-next-line jsx-a11y/click-events-have-key-events
            <li
              key={optionKey(option)}
              id={optionId(index)}
              role="option"
              aria-selected={index === activeIndex}
              onMouseMove={() => setActive(index)}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => select(option)}
              className={cx(
                paletteRow,
                option.kind !== 'matter' &&
                  css({ color: 'primary', fontWeight: 500 })
              )}
            >
              <OptionContent option={option} query={name} />
              {index === activeIndex && <Kbd>↵</Kbd>}
            </li>
          ))}
        </ul>
      </div>
      <PaletteFooter escapeLabel={t('classification.close')} onEscape={onClose}>
        <span className={hint}>
          <Kbd>↑↓</Kbd>
          {t('classification.hintNavigate')}
        </span>
        <span className={hint}>
          <Kbd>↵</Kbd>
          {t('classification.hintChoose')}
        </span>
      </PaletteFooter>
    </>
  )
}

const paletteRow = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.625rem',
  minHeight: '2.5rem',
  padding: '0.375rem 0.625rem',
  borderRadius: '9px',
  color: 'var(--heading-foreground)',
  cursor: 'pointer',
  '&[aria-selected="true"]': { backgroundColor: 'accent' },
})

const hint = css({
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.375rem',
})

const iconSlot = css({
  display: 'grid',
  placeItems: 'center',
  width: '22px',
  height: '22px',
  flexShrink: 0,
  borderRadius: '6px',
})

function OptionContent({
  option,
  query,
}: {
  option: PaletteOption
  query: string
}) {
  const { t } = useTranslation('meetingHistory')
  switch (option.kind) {
    case 'matter':
      return (
        <>
          <img src={DOSSIER_ICON} alt="" width={22} height={22} />
          <span
            className={css({ flex: 1, minWidth: 0, overflowWrap: 'anywhere' })}
          >
            <MatchedLabel label={option.matter.display_label} query={query} />
          </span>
        </>
      )
    case 'more':
      return <span className={css({ flex: 1 })}>{t('loadMore')}</span>
    case 'create':
      return (
        <>
          <span className={cx(iconSlot, css({ backgroundColor: 'accent' }))}>
            <AddIcon size={14} aria-hidden="true" />
          </span>
          <span
            className={css({ flex: 1, minWidth: 0, overflowWrap: 'anywhere' })}
          >
            {query
              ? t('classification.createNamed', { name: query })
              : t('classification.create')}
          </span>
        </>
      )
  }
}

/** The part of the name that matches the search, in the primary colour. */
function MatchedLabel({ label, query }: { label: string; query: string }) {
  const start = query ? label.toLowerCase().indexOf(query.toLowerCase()) : -1
  if (start < 0) return <>{label}</>
  const end = start + query.length
  return (
    <>
      {label.slice(0, start)}
      <b className={css({ fontWeight: 600, color: 'primary' })}>
        {label.slice(start, end)}
      </b>
      {label.slice(end)}
    </>
  )
}

/** What the list cannot show by itself: loading, failure, no match. */
function SearchState({
  search,
  query,
  onRetry,
}: {
  search: DestinationSearch
  query: string
  onRetry: () => void
}): ReactNode {
  const { t } = useTranslation('meetingHistory')
  switch (search.phase) {
    case 'idle':
      return null
    case 'loading':
      return (
        <div role="status" aria-label={t('classification.loading')}>
          {[0, 1, 2].map((row) => (
            <div key={row} aria-hidden="true" className={paletteRow}>
              <span
                className={cx(iconSlot, css({ backgroundColor: 'muted' }))}
              />
              <span
                className={css({
                  width: '55%',
                  height: '0.625rem',
                  borderRadius: '5px',
                  backgroundColor: 'muted',
                })}
              />
            </div>
          ))}
        </div>
      )
    case 'error':
      return (
        <p role="alert" className={stateLine}>
          <WarningIcon size={15} aria-hidden="true" />
          {t('classification.searchFailed')}
          <Button variant="link" size="xs" onPress={onRetry}>
            {t('error.retry')}
          </Button>
        </p>
      )
    case 'ready':
      if (search.items.length) return null
      return (
        <p className={stateLine}>
          {query
            ? t('classification.noMatch', { query })
            : t('classification.empty')}
        </p>
      )
  }
}

const stateLine = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'center',
  gap: '0.375rem',
  margin: 0,
  padding: '0.5rem 0.625rem',
  color: 'muted-foreground',
  fontSize: '0.8125rem',
  lineHeight: '1.25rem',
  '&[role="alert"]': { color: 'destructive' },
})
