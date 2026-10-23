import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import { MeetingAssistant } from '@/features/meeting-assistant/MeetingAssistant'
import type {
  MeetingAssistantCitation,
  MeetingAssistantSendRequest,
  MeetingAssistantThread,
  MeetingAssistantTurn,
} from '@/features/meeting-assistant/types'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
const api = vi.mocked(fetchApi)
const meetingId = 'meeting_0123456789abcdef'
const endpoint = `meetings/history/${meetingId}/assistant/`
const citation: MeetingAssistantCitation = {
  citation_id: 'citation_0123456789abcdef',
  meeting_ref: meetingId,
  source_kind: 'reviewed_revision',
  source_ref: 'source_0123456789abcdef',
  source_version: 3,
  source_digest: 'a'.repeat(64),
  segment_id: 'segment-1',
  start_ms: 12000,
  end_ms: 18000,
  time_basis: 'source_relative_ms',
}
function turn(commandId = 'command_0123456789abcdef'): MeetingAssistantTurn {
  return {
    command_id: commandId,
    operation: 'question',
    question: 'Quel budget ?',
    state: 'completed',
    created_at: 1,
    coverage: {
      state: 'partial',
      source_coverage: 'partial_sources',
      selection: 'relevant_passages',
      total_segments: 5,
      selected_segments: 2,
      omitted_segments: 3,
    },
    result: {
      state: 'answered',
      text: 'Budget à confirmer.',
      rationale_category: 'supported',
      citations: [citation],
      actions: [],
      email_draft: null,
      publication_state: 'private_proposal',
    },
  }
}
function thread(
  turns: MeetingAssistantTurn[] = [],
  version = 0
): MeetingAssistantThread {
  return {
    version: 1,
    meeting_ref: meetingId,
    thread_ref: 'thread_0123456789abcdef',
    thread_version: version,
    visibility: 'private',
    deleted: false,
    turns,
    has_more: false,
    next_cursor: null,
  }
}
function receipt(
  command: MeetingAssistantSendRequest,
  state?: MeetingAssistantTurn
) {
  const result = state ?? {
    ...turn(command.command_id),
    operation: command.operation,
    question: command.question,
  }
  return {
    version: 1,
    thread: thread([result], command.expected_thread_version + 1),
    turn: result,
    idempotent_replay: false,
  }
}
function setup(
  identityKey = 'author-a',
  readiness: 'preparing' | 'ready' | 'unavailable' = 'ready'
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const view = (identity: string) => (
    <QueryClientProvider client={client}>
      <MeetingAssistant
        key={identity}
        meetingId={meetingId}
        identityKey={identity}
        readiness={readiness}
        onClose={vi.fn()}
      />
    </QueryClientProvider>
  )
  const result = render(view(identityKey))
  return {
    ...result,
    client,
    switchAuthor: (identity: string) => result.rerender(view(identity)),
  }
}
/**
 * Matches an answer by its whole paragraph: one that just arrived is split
 * into words that write themselves out.
 */
const answer = (text: string) => (_: string, element: Element | null) =>
  element?.tagName === 'P' && element.textContent === text
async function openClearConfirmation() {
  // jsdom has no CSS.escape, which the open menu uses to focus its items.
  vi.stubGlobal('CSS', { escape: (value: string) => value })
  fireEvent.click(screen.getByRole('button', { name: 'options' }))
  fireEvent.click(await screen.findByRole('menuitem', { name: 'delete.open' }))
}
/** Sends a ready-made request from the composer's shortcuts menu. */
async function chooseShortcut(action: string) {
  // jsdom has no CSS.escape, which the open menu uses to focus its items.
  vi.stubGlobal('CSS', { escape: (value: string) => value })
  fireEvent.click(screen.getByRole('button', { name: 'actions.title' }))
  fireEvent.click(
    await screen.findByRole('menuitem', {
      name: new RegExp(`^actions\\.${action}`),
    })
  )
}
async function ask() {
  await screen.findByText('empty')
  fireEvent.change(screen.getByRole('textbox', { name: 'question' }), {
    target: { value: 'Quel budget ?' },
  })
  await waitFor(() =>
    expect(
      (screen.getByRole('button', { name: 'ask' }) as HTMLButtonElement)
        .disabled
    ).toBe(false)
  )
  fireEvent.click(screen.getByRole('button', { name: 'ask' }))
}
const writes = () =>
  api.mock.calls.filter(
    ([, options]) => options?.method === 'POST' || options?.method === 'DELETE'
  )
beforeEach(() => {
  api.mockReset()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('private assistant connected flow', () => {
  it('rereads the durable private thread without generating anything on mount', async () => {
    api.mockResolvedValue(thread([turn()], 2))
    setup()
    expect(await screen.findByText(answer('Budget à confirmer.'))).toBeTruthy()
    expect(writes()).toHaveLength(0)
    expect(api.mock.calls[0][0]).toBe(`${endpoint}?limit=20`)
    expect(api.mock.calls[0][1]?.cache).toBe('no-store')
  })
  it('does not read the conversation before the transcript is ready', () => {
    api.mockResolvedValue(thread([turn()], 2))
    setup('author-a', 'preparing')
    expect(screen.getByText('preparing.title')).toBeTruthy()
    expect(api).not.toHaveBeenCalled()
    expectSendingDisabled(true)
  })
  it('keeps an existing conversation clearable without a transcript', async () => {
    api.mockResolvedValue(thread([turn()], 2))
    setup('author-a', 'unavailable')
    expect(await screen.findByRole('button', { name: 'options' })).toBeTruthy()
    expect(screen.getByText('unavailable.title')).toBeTruthy()
    expectSendingDisabled(true)
  })
  it('retries the identical command after a lost response and an explicit reread', async () => {
    let sends = 0
    api.mockImplementation(async (_path, options) => {
      if (options?.method !== 'POST') return thread()
      sends++
      if (sends === 1) throw new ApiError(503, {})
      return receipt(JSON.parse(String(options.body)))
    })
    setup()
    await ask()
    await screen.findByText('failure.unconfirmed')
    expect(
      (
        screen.getByRole('button', {
          name: 'ask',
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'failure.retry' }))
    await screen.findByText(answer('Budget à confirmer.'))
    expect(writes()).toHaveLength(2)
    expect(writes()[0][1]?.body).toBe(writes()[1][1]?.body)
  })
  it('finds an accepted command after an ambiguous failure without sending it again', async () => {
    let accepted: MeetingAssistantTurn | null = null
    api.mockImplementation(async (_path, options) => {
      if (options?.method === 'POST') {
        const command = JSON.parse(String(options.body))
        accepted = turn(command.command_id)
        throw new ApiError(503, {})
      }
      return thread(accepted ? [accepted] : [], accepted ? 1 : 0)
    })
    setup()
    await ask()
    await screen.findByText('failure.unconfirmed')
    fireEvent.click(screen.getByRole('button', { name: 'failure.retry' }))
    await screen.findByText(answer('Budget à confirmer.'))
    expect(screen.queryByText('failure.unconfirmed')).toBeNull()
    expect(writes()).toHaveLength(1)
  })
  it('rereads a conflict and requires explicit resubmission at the new thread version', async () => {
    let version = 0
    api.mockImplementation(async (_path, options) => {
      if (options?.method !== 'POST') return thread([], version)
      if (!version) {
        version = 5
        throw new ApiError(409, {})
      }
      return receipt(JSON.parse(String(options.body)))
    })
    setup()
    await ask()
    await screen.findByText('failure.conflict')
    // The conversation was read again: nothing is left to retry.
    expect(screen.queryByRole('button', { name: 'failure.retry' })).toBeNull()
    expect(writes()).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'ask' }))
    await screen.findByText(answer('Budget à confirmer.'))
    const commands = writes().map(([, options]) =>
      JSON.parse(String(options?.body))
    )
    expect(commands[1].expected_thread_version).toBe(5)
    expect(commands[1].command_id).not.toBe(commands[0].command_id)
  })
  it('dispatches all three actions without a publication, task or email request', async () => {
    api.mockImplementation(async (_path, options) => {
      if (options?.method !== 'POST') return thread()
      const command = JSON.parse(
        String(options.body)
      ) as MeetingAssistantSendRequest
      const result = {
        ...turn(command.command_id),
        operation: command.operation,
        question: null,
      }
      result.result!.email_draft =
        command.operation === 'email_draft'
          ? { subject: 'À relire', body: 'Bonjour, voici notre proposition.' }
          : null
      return receipt(command, result)
    })
    setup()
    await screen.findByText('empty')
    for (const name of ['short-summary', 'actions', 'follow-up-email']) {
      await waitFor(() =>
        expect(
          (
            screen.getByRole('button', {
              name: 'actions.title',
            }) as HTMLButtonElement
          ).disabled
        ).toBe(false)
      )
      await chooseShortcut(name)
      await waitFor(() =>
        expect(
          (
            screen.getByRole('button', {
              name: 'actions.title',
            }) as HTMLButtonElement
          ).disabled
        ).toBe(false)
      )
    }
    expect(writes().map(([path]) => path)).toEqual([
      endpoint,
      endpoint,
      endpoint,
    ])
    expect(
      writes().map(([, options]) => JSON.parse(String(options?.body)).operation)
    ).toEqual(['summary', 'actions', 'email_draft'])
    expect(
      screen.getByRole('region', { name: 'email.label' }).textContent
    ).toContain('Bonjour, voici notre proposition.')
  })
  it('resolves the full citation descriptor and hides changed source versions', async () => {
    api.mockImplementation(async (path, options) => {
      if (path.endsWith('citation/'))
        return {
          version: 1,
          segment: {
            citation: { ...citation, source_version: 4 },
            text: 'Texte à ne pas afficher.',
            speaker: null,
            status: 'transcript',
          },
        }
      if (!options?.method) return thread([turn()], 1)
      throw new Error('Unexpected write')
    })
    setup()
    await screen.findByText(answer('Budget à confirmer.'))
    const source = screen.getByRole('button', {
      name: 'result.openSource',
    })
    fireEvent.click(source)
    expect(await screen.findByRole('alert')).toHaveProperty(
      'textContent',
      'source.error'
    )
    expect(screen.queryByText('Texte à ne pas afficher.')).toBeNull()
    expect(JSON.parse(String(writes()[0][1]?.body)).citation).toEqual(citation)
  })
  it('clears the previous author’s results and checks current rights again', async () => {
    let denied = false
    api.mockImplementation(async () => {
      if (denied) throw new ApiError(403, {})
      return thread([turn()], 1)
    })
    const view = setup()
    await screen.findByText(answer('Budget à confirmer.'))
    denied = true
    view.switchAuthor('author-b')
    expect(screen.queryByText(answer('Budget à confirmer.'))).toBeNull()
    await screen.findByText('error')
    expect(
      (
        screen.getByRole('button', {
          name: 'actions.title',
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
  })
  it('deletes only after confirmation and uses the current private thread version', async () => {
    api.mockImplementation(async (_path, options) => {
      if (options?.method === 'DELETE')
        return { version: 1, deleted: true, thread_version: 3 }
      return thread([turn()], 2)
    })
    setup()
    await screen.findByText(answer('Budget à confirmer.'))
    await openClearConfirmation()
    const dialog = await screen.findByRole('dialog', { name: 'delete.title' })
    expect(writes()).toHaveLength(0)
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'delete.confirm' })
    )
    await screen.findByText('empty')
    expect(writes()).toHaveLength(1)
    expect(writes()[0][0]).toBe(endpoint)
    expect(writes()[0][1]?.method).toBe('DELETE')
    expect(
      JSON.parse(String(writes()[0][1]?.body)).expected_thread_version
    ).toBe(2)
  })
  it('allows rereading a processing turn without issuing a second generation', async () => {
    let ready = false
    api.mockImplementation(async (_path, options) => {
      if (options?.method === 'POST') {
        const command = JSON.parse(String(options.body))
        return receipt(command, {
          ...turn(command.command_id),
          state: 'processing',
          result: null,
        })
      }
      return thread(ready ? [turn()] : [], ready ? 2 : 0)
    })
    setup()
    await ask()
    await screen.findByText('result.processing')
    expect(
      (
        screen.getByRole('button', {
          name: 'ask',
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    ready = true
    fireEvent.click(screen.getByRole('button', { name: 'refresh' }))
    await screen.findByText(answer('Budget à confirmer.'))
    expect(writes()).toHaveLength(1)
  })
  it('loads earlier exchanges in chronological order using the private cursor', async () => {
    const recent = turn('command_recent0123456789')
    recent.result!.text = 'Réponse récente'
    const older = turn('command_older0123456789')
    older.result!.text = 'Réponse précédente'
    api.mockImplementation(async (path) => {
      if (path.includes('before_command_id=')) return thread([older], 3)
      return {
        ...thread([recent], 3),
        has_more: true,
        next_cursor: recent.command_id,
      }
    })
    setup()
    await screen.findByText('Réponse récente')
    fireEvent.click(screen.getByRole('button', { name: 'older' }))
    await screen.findByText('Réponse précédente')
    const history = screen.getByRole('list', { name: 'history' }).textContent!
    expect(history.indexOf('Réponse précédente')).toBeLessThan(
      history.indexOf('Réponse récente')
    )
    expect(api.mock.calls[1][0]).toBe(
      `${endpoint}?limit=20&before_command_id=${recent.command_id}`
    )
    expect(writes()).toHaveLength(0)
  })
  it('hides already displayed answers when the same author loses access on reopening', async () => {
    let denied = false
    api.mockImplementation(async () => {
      if (denied) throw new ApiError(403, {})
      return thread([turn()], 1)
    })
    const firstOpening = setup()
    await screen.findByText(answer('Budget à confirmer.'))
    denied = true
    firstOpening.unmount()
    setup()
    await screen.findByText('error')
    expect(screen.queryByText(answer('Budget à confirmer.'))).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'result.openSource' })
    ).toBeNull()
  })
  it('permits correction after a definite validation rejection without replaying invalid content', async () => {
    let rejected = false
    api.mockImplementation(async (_path, options) => {
      if (options?.method !== 'POST') return thread()
      if (!rejected) {
        rejected = true
        throw new ApiError(422, {})
      }
      return receipt(JSON.parse(String(options.body)))
    })
    setup()
    await ask()
    await screen.findByText('failure.rejected')
    fireEvent.change(screen.getByRole('textbox', { name: 'question' }), {
      target: { value: 'Quelle échéance ?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'ask' }))
    await screen.findByText(answer('Budget à confirmer.'))
    const commands = writes().map(([, options]) =>
      JSON.parse(String(options?.body))
    )
    expect(commands[1].question).toBe('Quelle échéance ?')
    expect(commands[1].command_id).not.toBe(commands[0].command_id)
  })
  it('recognizes a completed deletion after a lost receipt without deleting twice', async () => {
    let deleted = false
    api.mockImplementation(async (_path, options) => {
      if (options?.method === 'DELETE') {
        deleted = true
        throw new ApiError(503, {})
      }
      return deleted ? { ...thread([], 3), deleted: true } : thread([turn()], 2)
    })
    setup()
    await screen.findByText(answer('Budget à confirmer.'))
    await openClearConfirmation()
    const dialog = await screen.findByRole('dialog', { name: 'delete.title' })
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'delete.confirm' })
    )
    await within(dialog).findByText('command.unconfirmed')
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'command.retry' })
    )
    await screen.findByText('empty')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(writes()).toHaveLength(1)
  })
})

describe('terminal unknown assistant requests', () => {
  it.each([
    { shortcut: null, operation: 'question' },
    { shortcut: 'short-summary', operation: 'summary' },
    { shortcut: 'actions', operation: 'actions' },
    { shortcut: 'follow-up-email', operation: 'email_draft' },
  ])(
    'allows a new explicit $operation after reading and reopening unknown',
    async ({ shortcut, operation }) => {
      const unknown: MeetingAssistantTurn = {
        ...turn(),
        state: 'unknown',
        result: null,
      }
      let finish!: () => void
      const response = new Promise<void>((resolve) => {
        finish = resolve
      })
      api.mockImplementation(async (_path, options) => {
        if (options?.method !== 'POST') return thread([unknown], 4)
        await response
        return receipt(JSON.parse(String(options.body)))
      })
      const firstPage = setup()
      await screen.findByText('result.unknown')
      await waitFor(() =>
        expect(
          (
            screen.getByRole('button', {
              name: 'actions.title',
            }) as HTMLButtonElement
          ).disabled
        ).toBe(false)
      )
      expect(writes()).toHaveLength(0)
      firstPage.unmount()
      setup()
      await screen.findByText('result.unknown')
      fireEvent.change(screen.getByRole('textbox', { name: 'question' }), {
        target: { value: 'Nouvelle question explicite' },
      })
      await waitFor(() => expectSendingDisabled(false))
      expect(writes()).toHaveLength(0)
      if (shortcut) await chooseShortcut(shortcut)
      else fireEvent.click(screen.getByRole('button', { name: 'ask' }))
      await waitFor(() => expectSendingDisabled(true))
      expect(writes()).toHaveLength(1)
      const command = JSON.parse(String(writes()[0][1]?.body))
      expect(command.operation).toBe(operation)
      expect(command.command_id).not.toBe(unknown.command_id)
      expect(command.expected_thread_version).toBe(4)
      finish()
      await screen.findByText(answer('Budget à confirmer.'))
      expect(writes()).toHaveLength(1)
    }
  )

  it('keeps processing blocking through rereads, then permits a new command when Core expires it to unknown', async () => {
    let state: MeetingAssistantTurn['state'] = 'processing'
    const previous = turn()
    api.mockImplementation(async (_path, options) => {
      if (options?.method === 'POST')
        return receipt(JSON.parse(String(options.body)))
      return thread([{ ...previous, state, result: null }], 3)
    })
    setup()
    await screen.findByText('result.processing')
    expectSendingDisabled(true)
    fireEvent.click(screen.getByRole('button', { name: 'refresh' }))
    await waitFor(() => expect(screen.queryByText('loading')).toBeNull())
    expectSendingDisabled(true)
    expect(writes()).toHaveLength(0)
    state = 'unknown'
    fireEvent.click(screen.getByRole('button', { name: 'refresh' }))
    await screen.findByText('result.unknown')
    fireEvent.change(screen.getByRole('textbox', { name: 'question' }), {
      target: { value: 'Une autre question' },
    })
    await waitFor(() => expectSendingDisabled(false))
    expect(writes()).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'ask' }))
    await screen.findByText(answer('Budget à confirmer.'))
    expect(writes()).toHaveLength(1)
    expect(JSON.parse(String(writes()[0][1]?.body)).command_id).not.toBe(
      previous.command_id
    )
  })

  it('reconciles an uncertain send found as terminal unknown without retrying that command', async () => {
    let accepted: MeetingAssistantTurn | null = null
    api.mockImplementation(async (_path, options) => {
      if (options?.method !== 'POST')
        return thread(accepted ? [accepted] : [], accepted ? 2 : 0)
      const command = JSON.parse(String(options.body))
      if (!accepted) {
        accepted = {
          ...turn(command.command_id),
          state: 'unknown',
          result: null,
        }
        throw new ApiError(503, {})
      }
      return receipt(command)
    })
    setup()
    await ask()
    await screen.findByText('failure.unconfirmed')
    expectSendingDisabled(true)
    fireEvent.click(screen.getByRole('button', { name: 'failure.retry' }))
    await screen.findByText('result.unknown')
    await waitFor(() => expectSendingDisabled(false))
    expect(screen.queryByRole('button', { name: 'failure.retry' })).toBeNull()
    expect(writes()).toHaveLength(1)
    await chooseShortcut('short-summary')
    await screen.findByText(answer('Budget à confirmer.'))
    const commands = writes().map(([, options]) =>
      JSON.parse(String(options?.body))
    )
    expect(commands).toHaveLength(2)
    expect(commands[1].command_id).not.toBe(commands[0].command_id)
    expect(commands[1].expected_thread_version).toBe(2)
  })

  it('replays the same uncertain send when reread finds only another terminal unknown command', async () => {
    let sent = false
    api.mockImplementation(async (_path, options) => {
      if (options?.method === 'POST') {
        sent = true
        throw new ApiError(503, {})
      }
      if (sent)
        return thread([{ ...turn(), state: 'unknown', result: null }], 2)
      return thread()
    })
    setup()
    await ask()
    await screen.findByText('failure.unconfirmed')
    fireEvent.click(screen.getByRole('button', { name: 'failure.retry' }))
    await screen.findByText('result.unknown')
    await waitFor(() => expect(writes()).toHaveLength(2))
    expect(writes()[1][1]?.body).toBe(writes()[0][1]?.body)
    await screen.findByText('failure.unconfirmed')
    expectSendingDisabled(true)
  })
})

function expectSendingDisabled(disabled: boolean) {
  for (const name of ['ask', 'actions.title'])
    expect(
      (screen.getByRole('button', { name }) as HTMLButtonElement).disabled
    ).toBe(disabled)
}
