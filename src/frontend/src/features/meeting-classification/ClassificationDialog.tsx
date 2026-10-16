import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Dialog, Field } from '@/primitives'
import { css } from '@/styled-system/css'

export type MatterDestination = { matter_ref: string; display_label: string }
export type DestinationSearch = {
  phase: 'idle' | 'loading' | 'ready' | 'error'
  items: MatterDestination[]
  hasMore: boolean
}

/** One explicit selection/confirmation flow for both meeting entry points. */
export function ClassificationDialog({
  isOpen,
  onClose,
  search,
  onSearch,
  onLoadMore,
  onClassify,
  onCreate,
  pending,
  error,
  createdMatter,
}: {
  isOpen: boolean
  onClose: () => void
  search: DestinationSearch
  onSearch: (query: string) => void
  onLoadMore: () => void
  onClassify: (matter: MatterDestination) => void
  onCreate: (label: string) => void
  pending: boolean
  error: 'conflict' | 'failed' | null
  createdMatter: MatterDestination | null
}) {
  const { t } = useTranslation('meetingHistory')
  const [query, setQuery] = useState('')
  const [label, setLabel] = useState('')
  const [selected, setSelected] = useState<MatterDestination | null>(null)
  const [creating, setCreating] = useState(false)
  const destination = createdMatter ?? selected

  function renderStep() {
    if (createdMatter)
      return (
        <p role="status">
          {t('classification.created', { name: createdMatter.display_label })}
        </p>
      )
    if (creating)
      return (
        <Field
          type="text"
          label={t('classification.name')}
          value={label}
          onChange={setLabel}
          maxLength={200}
          isRequired
          isDisabled={pending}
        />
      )
    return (
      <>
        <form
          onSubmit={(event) => {
            event.preventDefault()
            setSelected(null)
            onSearch(query.trim())
          }}
        >
          <Field
            type="text"
            label={t('classification.search')}
            value={query}
            onChange={setQuery}
            maxLength={100}
            isDisabled={pending}
          />
          <Button type="submit" variant="secondary" isDisabled={pending}>
            {t('classification.search')}
          </Button>
        </form>
        <DestinationResults
          search={search}
          selected={selected?.matter_ref}
          onSelect={setSelected}
          pending={pending}
        />
        {search.hasMore && (
          <Button
            variant="ghost"
            isDisabled={pending || search.phase === 'loading'}
            onPress={onLoadMore}
          >
            {t('loadMore')}
          </Button>
        )}
        <Button
          variant="outline"
          isDisabled={pending}
          onPress={() => {
            setSelected(null)
            setCreating(true)
          }}
        >
          {t('classification.create')}
        </Button>
      </>
    )
  }

  return (
    <Dialog
      title={t('classification.title')}
      appearance="app"
      isOpen={isOpen}
      onOpenChange={(open) => {
        if (!open && !pending) onClose()
      }}
    >
      <div
        className={css({
          display: 'flex',
          flexDirection: 'column',
          gap: '1rem',
        })}
      >
        <p>{t('classification.sharing')}</p>
        {renderStep()}
        {destination && !createdMatter && (
          <p role="status">
            {t('classification.selected', { name: destination.display_label })}
          </p>
        )}
        {error && <p role="alert">{t(`classification.${error}`)}</p>}
        <div
          className={css({
            display: 'flex',
            flexWrap: 'wrap',
            gap: '0.5rem',
            justifyContent: 'flex-end',
          })}
        >
          <Button variant="ghost" isDisabled={pending} onPress={onClose}>
            {t('classification.cancel')}
          </Button>
          {creating && !createdMatter ? (
            <Button
              isDisabled={pending || !label.trim()}
              loading={pending}
              onPress={() => onCreate(label.trim())}
            >
              {t('classification.createAndClassify')}
            </Button>
          ) : (
            <Button
              isDisabled={pending || !destination}
              loading={pending}
              onPress={() => {
                if (destination) onClassify(destination)
              }}
            >
              {t(
                createdMatter
                  ? 'classification.resume'
                  : 'classification.confirm'
              )}
            </Button>
          )}
        </div>
      </div>
    </Dialog>
  )
}

function DestinationResults({
  search,
  selected,
  onSelect,
  pending,
}: {
  search: DestinationSearch
  selected?: string
  onSelect: (matter: MatterDestination) => void
  pending: boolean
}) {
  const { t } = useTranslation('meetingHistory')
  switch (search.phase) {
    case 'idle':
      return null
    case 'loading':
      return <p role="status">{t('classification.loading')}</p>
    case 'error':
      return <p role="alert">{t('classification.searchFailed')}</p>
    case 'ready':
      if (!search.items.length) return <p>{t('classification.empty')}</p>
      return (
        <ul
          aria-label={t('classification.destinations')}
          className={css({
            padding: 0,
            listStyle: 'none',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.5rem',
          })}
        >
          {search.items.map((matter) => (
            <li key={matter.matter_ref}>
              <Button
                variant="outline"
                aria-pressed={selected === matter.matter_ref}
                isDisabled={pending}
                onPress={() => onSelect(matter)}
              >
                {matter.display_label}
              </Button>
            </li>
          ))}
        </ul>
      )
  }
}
