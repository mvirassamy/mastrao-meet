import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ClassificationDialog,
  type DestinationSearch,
} from '@/features/meeting-classification/ClassificationDialog'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { name?: string; query?: string }) =>
      [key, options?.name ?? options?.query].filter(Boolean).join(': '),
  }),
}))
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

const folders: DestinationSearch = {
  phase: 'ready',
  query: '',
  hasMore: false,
  items: [
    { matter_ref: 'matter_a', display_label: 'Fontaine c/ Martin' },
    { matter_ref: 'matter_b', display_label: 'Succession Fontaine' },
  ],
}

const renderDialog = (
  props: Partial<Parameters<typeof ClassificationDialog>[0]> = {}
) => {
  const handlers = {
    onClose: vi.fn(),
    onSearch: vi.fn(),
    onLoadMore: vi.fn(),
    onClassify: vi.fn(),
    onCreate: vi.fn(),
    clearError: vi.fn(),
  }
  const dialog = (next: typeof props = {}) => (
    <ClassificationDialog
      isOpen
      search={folders}
      pending={false}
      error={null}
      createdMatter={null}
      {...handlers}
      {...props}
      {...next}
    />
  )
  const view = render(dialog())
  return {
    ...handlers,
    rerender: (next: typeof props) => view.rerender(dialog(next)),
  }
}

const searchField = () =>
  screen.getByRole('combobox', { name: 'classification.search' })

describe('classification palette', () => {
  it('picks a folder with the arrows, confirms it, and goes back with Escape', () => {
    const { onClassify, onClose } = renderDialog()
    const field = searchField()
    fireEvent.keyDown(field, { key: 'ArrowDown' })
    const active = screen.getByRole('option', { selected: true })
    expect(field.getAttribute('aria-activedescendant')).toBe(active.id)
    expect(active.textContent).toContain('Succession Fontaine')

    fireEvent.keyDown(field, { key: 'Enter' })
    expect(screen.getByText('classification.sharing')).toBeTruthy()
    expect(screen.getByText('Succession Fontaine')).toBeTruthy()

    fireEvent.keyDown(
      screen.getByRole('button', { name: 'classification.classifyHere' }),
      { key: 'Escape' }
    )
    expect(searchField()).toBeTruthy()
    expect(onClose).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('option', { name: /Fontaine c\/ Martin/ }))
    fireEvent.click(
      screen.getByRole('button', { name: 'classification.classifyHere' })
    )
    expect(onClassify).toHaveBeenCalledWith(folders.items[0])
  })

  it('searches after a pause and offers to create the typed name', () => {
    vi.useFakeTimers()
    const { onSearch, onCreate, rerender } = renderDialog()
    fireEvent.change(searchField(), { target: { value: ' Durand ' } })
    expect(onSearch).not.toHaveBeenCalled()
    act(() => vi.advanceTimersByTime(250))
    expect(onSearch).toHaveBeenCalledWith('Durand')
    rerender({
      search: { phase: 'ready', query: 'Durand', hasMore: false, items: [] },
    })
    expect(screen.getByText('classification.noMatch: Durand')).toBeTruthy()

    fireEvent.keyDown(searchField(), { key: 'Enter' })
    const name = screen.getByRole('textbox', { name: /classification\.name/ })
    expect((name as HTMLInputElement).value).toBe('Durand')
    fireEvent.click(
      screen.getByRole('button', { name: 'classification.createAndClassify' })
    )
    expect(onCreate).toHaveBeenCalledWith('Durand')
  })

  it('ignores Enter while the folders of the typed search load', () => {
    const { onCreate, onClassify } = renderDialog({
      search: { phase: 'loading', query: 'Durand', hasMore: false, items: [] },
    })
    fireEvent.change(searchField(), { target: { value: 'Durand' } })
    fireEvent.keyDown(searchField(), { key: 'Enter' })
    expect(searchField()).toBeTruthy()
    expect(
      screen.queryByRole('textbox', { name: /classification\.name/ })
    ).toBeNull()
    expect(onCreate).not.toHaveBeenCalled()
    expect(onClassify).not.toHaveBeenCalled()
  })

  it('keeps the way back closed while a filing is pending', () => {
    const createdMatter = { matter_ref: 'matter_new', display_label: 'Durand' }
    renderDialog({ createdMatter, pending: true })
    expect(
      screen
        .getByRole('button', { name: 'classification.close' })
        .hasAttribute('disabled')
    ).toBe(true)
  })

  it('searches at once on Enter instead of choosing from an older list', () => {
    const { onSearch, onClassify } = renderDialog()
    fireEvent.change(searchField(), { target: { value: 'Durand' } })
    fireEvent.keyDown(searchField(), { key: 'Enter' })
    expect(onSearch).toHaveBeenCalledWith('Durand')
    expect(searchField()).toBeTruthy()
    expect(screen.queryByText('classification.sharing')).toBeNull()
    expect(onClassify).not.toHaveBeenCalled()
  })

  it('loads more folders from the last row', () => {
    const { onLoadMore } = renderDialog({
      search: { ...folders, hasMore: true },
    })
    const list = screen.getByRole('listbox')
    fireEvent.click(within(list).getByRole('option', { name: /loadMore/ }))
    expect(onLoadMore).toHaveBeenCalled()
  })

  it('resumes a created folder without offering to choose another one', () => {
    const createdMatter = { matter_ref: 'matter_new', display_label: 'Durand' }
    const { onClassify } = renderDialog({ createdMatter, error: 'failed' })
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'classification.back' })
    ).toBeNull()
    expect(screen.getByRole('alert').textContent).toContain(
      'classification.failed'
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'classification.resume' })
    )
    expect(onClassify).toHaveBeenCalledWith(createdMatter)
  })
})
