import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PersonalNotesController } from './PersonalNotesController'
import { PERSONAL_NOTE_LIMIT } from './noteContent'
import {
  PersonalNoteRequestError,
  type PersonalNote,
  type PersonalNotesApi,
} from '../api/personalNotesApi'

function note(content = '', revision = 0): PersonalNote {
  return {
    id: revision ? 'private-note' : null,
    content,
    revision,
    created_at: revision ? '2026-10-16T12:00:00Z' : null,
    updated_at: revision ? '2026-10-16T12:00:00Z' : null,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

const controllers: PersonalNotesController[] = []
function setup(initial = note()) {
  const read = vi.fn<PersonalNotesApi['read']>().mockResolvedValue(initial)
  const write = vi
    .fn<PersonalNotesApi['write']>()
    .mockImplementation(async (content, revision) =>
      note(content, revision + 1)
    )
  const controller = new PersonalNotesController({ read, write })
  controllers.push(controller)
  controller.start()
  return { controller, read, write, loaded: controller.retry() }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  controllers.forEach((controller) => controller.stop())
  controllers.length = 0
  vi.useRealTimers()
})

describe('personal note autosave', () => {
  it('does not create an empty document on read or an unchanged flush', async () => {
    const { controller, write, loaded } = setup()
    await loaded
    expect(await controller.flush()).toBe(true)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(write).not.toHaveBeenCalled()
    expect(controller.getSnapshot().hasUnsavedChanges).toBe(false)
  })

  it('debounces edits for 800ms and keeps plaintext unchanged', async () => {
    const { controller, write, loaded } = setup()
    await loaded
    controller.setContent('first')
    await vi.advanceTimersByTimeAsync(799)
    expect(write).not.toHaveBeenCalled()
    controller.setContent('<script>plain text</script>\n• second')
    await vi.advanceTimersByTimeAsync(799)
    expect(write).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(write).toHaveBeenCalledExactlyOnceWith(
      '<script>plain text</script>\n• second',
      0,
      expect.any(AbortSignal)
    )
    expect(controller.getSnapshot().status).toBe('ready')
  })

  it('saves within five seconds of continuous typing', async () => {
    const { controller, write, loaded } = setup()
    await loaded
    for (let index = 0; index < 13; index += 1) {
      controller.setContent(`draft ${index}`)
      await vi.advanceTimersByTimeAsync(400)
    }
    expect(write).toHaveBeenCalledOnce()
    expect(write.mock.calls[0][0]).toBe('draft 12')
  })

  it('serializes saves and acknowledges only the sent snapshot', async () => {
    const { controller, write, loaded } = setup()
    await loaded
    const first = deferred<PersonalNote>()
    const second = deferred<PersonalNote>()
    write.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    controller.setContent('snapshot')
    const flushing = controller.flush()
    await vi.advanceTimersByTimeAsync(0)
    controller.setContent('newer edits')
    expect(controller.flush()).toBe(flushing)
    await vi.advanceTimersByTimeAsync(6_000)
    expect(write).toHaveBeenCalledOnce()
    first.resolve(note('snapshot', 1))
    await vi.advanceTimersByTimeAsync(0)
    expect(controller.getSnapshot().content).toBe('newer edits')
    expect(controller.getSnapshot().hasUnsavedChanges).toBe(true)
    expect(write.mock.calls[1].slice(0, 2)).toEqual(['newer edits', 1])
    second.resolve(note('newer edits', 2))
    expect(await flushing).toBe(true)
    expect(controller.getSnapshot().hasUnsavedChanges).toBe(false)
  })

  it('never writes after a failed load and can retry loading safely', async () => {
    const { controller, read, write, loaded } = setup()
    read.mockRejectedValueOnce(new Error('offline'))
    expect(await loaded).toBe(false)
    controller.setContent('cannot edit an unknown baseline')
    expect(await controller.flush()).toBe(false)
    await vi.advanceTimersByTimeAsync(6_000)
    expect(write).not.toHaveBeenCalled()
    expect(controller.getSnapshot().hasLoaded).toBe(false)
    read.mockResolvedValue(note('existing', 4))
    expect(await controller.retry()).toBe(true)
    expect(controller.getSnapshot().content).toBe('existing')
    expect(write).not.toHaveBeenCalled()
  })

  it('retains a draft on 409, reads the server, and loads it only on request', async () => {
    const { controller, read, write, loaded } = setup(note('base', 2))
    await loaded
    read.mockResolvedValue(note('other tab', 3))
    write.mockRejectedValueOnce(new PersonalNoteRequestError(409))
    controller.setContent('my draft')
    expect(await controller.flush()).toBe(false)
    expect(read).toHaveBeenCalledTimes(2)
    expect(controller.getSnapshot()).toMatchObject({
      content: 'my draft',
      status: 'conflict',
      conflict: note('other tab', 3),
    })
    controller.setContent('still editable')
    await vi.advanceTimersByTimeAsync(6_000)
    expect(write).toHaveBeenCalledOnce()
    controller.loadSavedVersion()
    expect(controller.getSnapshot()).toMatchObject({
      content: 'other tab',
      status: 'ready',
      hasUnsavedChanges: false,
    })
  })

  it('uses the fetched conflict revision when explicitly keeping the draft', async () => {
    const { controller, read, write, loaded } = setup(note('base', 2))
    await loaded
    read.mockResolvedValue(note('other tab', 3))
    write.mockRejectedValueOnce(new PersonalNoteRequestError(409))
    controller.setContent('my draft')
    await controller.flush()
    expect(await controller.keepDraft()).toBe(true)
    expect(write.mock.calls[1].slice(0, 2)).toEqual(['my draft', 3])
  })

  it('keeps 409 recovery explicit even when its initial GET fails', async () => {
    const { controller, read, write, loaded } = setup(note('base', 1))
    await loaded
    write.mockRejectedValueOnce(new PersonalNoteRequestError(409))
    read.mockRejectedValueOnce(new Error('offline'))
    controller.setContent('draft')
    expect(await controller.flush()).toBe(false)
    read.mockResolvedValue(note('other', 2))
    expect(await controller.retry()).toBe(false)
    expect(controller.getSnapshot()).toMatchObject({
      content: 'draft',
      status: 'conflict',
    })
    expect(write).toHaveBeenCalledOnce()
  })

  it('recovers a lost acknowledgement without replaying the saved snapshot', async () => {
    const { controller, read, write, loaded } = setup(note('base', 1))
    await loaded
    write.mockRejectedValueOnce(new Error('response lost'))
    controller.setContent('sent snapshot')
    await controller.flush()
    controller.setContent('newer local text')
    read.mockResolvedValue(note('sent snapshot', 2))
    expect(await controller.retry()).toBe(true)
    expect(write.mock.calls[1].slice(0, 2)).toEqual(['newer local text', 2])
    expect(controller.getSnapshot().content).toBe('newer local text')
  })

  it('rereads an unchanged baseline before retrying the latest draft', async () => {
    const { controller, read, write, loaded } = setup(note('base', 1))
    await loaded
    write.mockRejectedValueOnce(new Error('offline'))
    controller.setContent('first draft')
    await controller.flush()
    controller.setContent('latest draft')
    expect(await controller.retry()).toBe(true)
    expect(read).toHaveBeenCalledTimes(2)
    expect(write.mock.calls[1].slice(0, 2)).toEqual(['latest draft', 1])
  })

  it('confirms a lost acknowledgement without another PUT when no new edits exist', async () => {
    const { controller, read, write, loaded } = setup(note('base', 1))
    await loaded
    write.mockRejectedValueOnce(new Error('response lost'))
    controller.setContent('saved despite failure')
    expect(await controller.flush()).toBe(false)
    read.mockResolvedValue(note('saved despite failure', 2))
    expect(await controller.retry()).toBe(true)
    expect(write).toHaveBeenCalledOnce()
    expect(controller.getSnapshot().hasUnsavedChanges).toBe(false)
  })

  it('never repeats an uncertain PUT when the recovery GET also fails', async () => {
    const { controller, read, write, loaded } = setup(note('base', 1))
    await loaded
    write.mockRejectedValueOnce(new Error('response lost'))
    controller.setContent('private draft')
    await controller.flush()
    read.mockRejectedValueOnce(new Error('offline'))
    expect(await controller.retry()).toBe(false)
    controller.setContent('continued draft')
    await vi.advanceTimersByTimeAsync(6_000)
    expect(write).toHaveBeenCalledOnce()
    expect(controller.getSnapshot()).toMatchObject({
      content: 'continued draft',
      status: 'error',
      hasUnsavedChanges: true,
    })
  })

  it('requires conflict resolution if recovery finds another server version', async () => {
    const { controller, read, write, loaded } = setup(note('base', 1))
    await loaded
    write.mockRejectedValueOnce(new Error('response lost'))
    controller.setContent('local')
    await controller.flush()
    read.mockResolvedValue(note('different', 3))
    expect(await controller.retry()).toBe(false)
    expect(controller.getSnapshot()).toMatchObject({
      content: 'local',
      status: 'conflict',
    })
    expect(write).toHaveBeenCalledOnce()
  })

  it('treats reverting to the old baseline as unsaved after an uncertain write', async () => {
    const { controller, read, write, loaded } = setup(note('base', 1))
    await loaded
    write.mockRejectedValueOnce(new Error('response lost'))
    controller.setContent('sent')
    await controller.flush()
    controller.setContent('base')
    expect(controller.getSnapshot().hasUnsavedChanges).toBe(true)
    read.mockResolvedValue(note('sent', 2))
    expect(await controller.retry()).toBe(true)
    expect(write.mock.calls[1].slice(0, 2)).toEqual(['base', 2])
  })

  it('suspends writes after authorization expires while retaining the draft', async () => {
    const { controller, write, loaded } = setup()
    await loaded
    write.mockRejectedValueOnce(new PersonalNoteRequestError(403))
    controller.setContent('private draft')
    await controller.flush()
    controller.setContent('private draft continued')
    await vi.advanceTimersByTimeAsync(6_000)
    expect(controller.getSnapshot()).toMatchObject({
      content: 'private draft continued',
      status: 'unauthorized',
    })
    expect(await controller.flush()).toBe(false)
    expect(write).toHaveBeenCalledOnce()
  })

  it('accepts the Unicode code point limit and persists an intentional clear', async () => {
    const { controller, write, loaded } = setup()
    await loaded
    controller.setContent('😀'.repeat(PERSONAL_NOTE_LIMIT))
    expect(controller.getSnapshot()).toMatchObject({
      length: PERSONAL_NOTE_LIMIT,
      wasRejected: false,
    })
    expect(controller.getSnapshot().content.length).toBe(
      PERSONAL_NOTE_LIMIT * 2
    )
    expect(await controller.flush()).toBe(true)
    expect(write.mock.calls[0][0]).toBe('😀'.repeat(PERSONAL_NOTE_LIMIT))
    controller.setContent('')
    expect(await controller.flush()).toBe(true)
    expect(write.mock.calls[1].slice(0, 2)).toEqual(['', 1])
  })

  it.each([0, PERSONAL_NOTE_LIMIT / 2])(
    'rejects an emoji insertion at code point %i without changing the saved document',
    async (position) => {
      const original = '😀'.repeat(PERSONAL_NOTE_LIMIT - 1) + 'Z'
      const { controller, write, loaded } = setup(note(original, 1))
      await loaded
      const characters = Array.from(original)
      characters.splice(position, 0, '🐈')
      controller.setContent(characters.join(''))
      expect(controller.getSnapshot().content).toBe(original)
      expect(controller.getSnapshot()).toMatchObject({
        length: PERSONAL_NOTE_LIMIT,
        wasRejected: true,
        hasUnsavedChanges: false,
      })
      await vi.advanceTimersByTimeAsync(6_000)
      expect(await controller.flush()).toBe(true)
      expect(write).not.toHaveBeenCalled()
    }
  )

  it('waits for IME commit even beyond the maximum autosave interval', async () => {
    const { controller, write, loaded } = setup()
    await loaded
    controller.setComposing(true)
    controller.setContent('に')
    await vi.advanceTimersByTimeAsync(6_000)
    expect(await controller.flush()).toBe(false)
    expect(write).not.toHaveBeenCalled()
    controller.setContent('日本語')
    controller.setComposing(false)
    await vi.advanceTimersByTimeAsync(0)
    expect(write.mock.calls[0][0]).toBe('日本語')
  })

  it('does not send newly composing text after a previous save completes', async () => {
    const { controller, write, loaded } = setup()
    await loaded
    const pending = deferred<PersonalNote>()
    write.mockReturnValueOnce(pending.promise)
    controller.setContent('before')
    const flushing = controller.flush()
    await vi.advanceTimersByTimeAsync(0)
    controller.setComposing(true)
    controller.setContent('before 日本')
    pending.resolve(note('before', 1))
    expect(await flushing).toBe(false)
    expect(write).toHaveBeenCalledOnce()
    controller.setComposing(false)
    await vi.advanceTimersByTimeAsync(800)
    expect(write.mock.calls[1].slice(0, 2)).toEqual(['before 日本', 1])
  })

  it('preserves IME composition at the limit and rejects an overflowing commit', async () => {
    const { controller, write, loaded } = setup(
      note('😀'.repeat(PERSONAL_NOTE_LIMIT), 1)
    )
    await loaded
    controller.setComposing(true)
    controller.setContent('😀'.repeat(PERSONAL_NOTE_LIMIT) + 'に')
    expect(controller.getSnapshot().content.endsWith('に')).toBe(true)
    expect(await controller.flush()).toBe(false)
    controller.setComposing(false)
    expect(controller.getSnapshot()).toMatchObject({
      length: PERSONAL_NOTE_LIMIT,
      wasRejected: true,
    })
    expect(await controller.flush()).toBe(true)
    expect(write).not.toHaveBeenCalled()
  })

  it('restores and saves the precomposition draft after IME overflow', async () => {
    const { controller, write, loaded } = setup(note('saved version', 1))
    await loaded
    const draft = '😀'.repeat(PERSONAL_NOTE_LIMIT - 1) + 'Z'
    controller.setContent(draft)
    controller.setComposing(true)
    controller.setContent('日本' + draft)
    controller.setComposing(true)
    controller.setContent('日本語' + draft)
    await vi.advanceTimersByTimeAsync(6_000)
    expect(write).not.toHaveBeenCalled()
    controller.setComposing(false)
    expect(controller.getSnapshot().content).toBe(draft)
    expect(controller.getSnapshot()).toMatchObject({
      length: PERSONAL_NOTE_LIMIT,
      wasRejected: true,
      hasUnsavedChanges: true,
    })
    expect(await controller.flush()).toBe(true)
    expect(write).toHaveBeenCalledExactlyOnceWith(
      draft,
      1,
      expect.any(AbortSignal)
    )
  })

  it('ignores a late load after its scope stops', async () => {
    const { controller, read, loaded } = setup()
    const pending = deferred<PersonalNote>()
    read.mockReturnValueOnce(pending.promise)
    await vi.advanceTimersByTimeAsync(0)
    controller.stop()
    pending.resolve(note('old owner secret', 4))
    expect(await loaded).toBe(false)
    expect(controller.getSnapshot().content).toBe('')
  })
})
