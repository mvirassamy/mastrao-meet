import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import { useMeetingClassification } from '@/features/meeting-classification/useMeetingClassification'

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
const api = vi.mocked(fetchApi)
const meetingId = 'meeting_0123456789abcdef'
const target = {
  matter_ref: 'matter_0123456789abcdef',
  display_label: 'Dossier client',
}
const classification = (version = 0, matter: string | null = null) => ({
  version: 1,
  meeting_ref: meetingId,
  matter_ref: matter,
  classification_version: version,
  can_classify: true,
})
function Controls() {
  const flow = useMeetingClassification(meetingId, 'user-a')
  return (
    <>
      <span>{flow.current.isSuccess ? 'loaded' : 'waiting'}</span>
      <span>{flow.current.data?.matter_ref ?? 'unfiled'}</span>
      <span>{flow.error}</span>
      {flow.createdMatter && (
        <span>created: {flow.createdMatter.display_label}</span>
      )}
      <button onClick={() => flow.onCreate('Dossier client')}>create</button>
      <button onClick={() => flow.onClassify(target)}>file</button>
    </>
  )
}
function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <Controls />
    </QueryClientProvider>
  )
}
beforeEach(() => api.mockReset())
afterEach(cleanup)

describe('classification commands', () => {
  it('keeps the created folder and replays the same attachment after a lost response', async () => {
    let attachments = 0
    api.mockImplementation(async (path, options) => {
      if (!options?.method) return classification()
      if (path === 'matters/')
        return {
          version: 1,
          ...target,
          metadata_version: 1,
          idempotent_replay: false,
        }
      attachments++
      if (attachments === 1) throw new ApiError(503, {})
      return {
        ...classification(1, target.matter_ref),
        idempotent_replay: true,
      }
    })
    setup()
    await screen.findByText('loaded')
    fireEvent.click(screen.getByText('create'))
    fireEvent.click(screen.getByText('create'))
    await screen.findByText('failed')
    expect(screen.getByText('created: Dossier client')).toBeTruthy()
    fireEvent.click(screen.getByText('file'))
    await screen.findByText(target.matter_ref)
    expect(
      api.mock.calls.filter(
        ([path, options]) => path === 'matters/' && options?.method === 'POST'
      )
    ).toHaveLength(1)
    const commands = api.mock.calls.filter(
      ([path, options]) =>
        path.includes('classification/') && options?.method === 'POST'
    )
    expect(commands).toHaveLength(2)
    expect(commands[0][1]?.body).toBe(commands[1][1]?.body)
    expect(screen.queryByText('created: Dossier client')).toBeNull()
  })
  it('refreshes a conflicting classification and requires another explicit confirmation', async () => {
    let revision = 0
    let calls = 0
    api.mockImplementation(async (_path, options) => {
      if (!options?.method) return classification(revision)
      calls++
      if (calls === 1) {
        revision = 2
        throw new ApiError(409, {})
      }
      return {
        ...classification(3, target.matter_ref),
        idempotent_replay: false,
      }
    })
    setup()
    await screen.findByText('loaded')
    fireEvent.click(screen.getByText('file'))
    await screen.findByText('conflict')
    expect(calls).toBe(1)
    fireEvent.click(screen.getByText('file'))
    await screen.findByText(target.matter_ref)
    const commands = api.mock.calls
      .filter(([, options]) => options?.method === 'POST')
      .map(([, options]) => JSON.parse(String(options?.body)))
    expect(commands[0].expected_classification_version).toBe(0)
    expect(commands[1].expected_classification_version).toBe(2)
    expect(commands[0].command_id).not.toBe(commands[1].command_id)
  })
  it('does not submit without the current server capability', async () => {
    api.mockResolvedValue({ ...classification(), can_classify: false })
    setup()
    await screen.findByText('loaded')
    fireEvent.click(screen.getByText('file'))
    expect(
      api.mock.calls.filter(([, options]) => options?.method === 'POST')
    ).toHaveLength(0)
  })
  it('does not announce success for a receipt pointing to another folder', async () => {
    api.mockImplementation(async (_path, options) => {
      if (!options?.method) return classification()
      return {
        ...classification(1, 'matter_other0123456789'),
        idempotent_replay: false,
      }
    })
    setup()
    await screen.findByText('loaded')
    fireEvent.click(screen.getByText('file'))
    await screen.findByText('failed')
    await waitFor(() => expect(screen.getByText('unfiled')).toBeTruthy())
  })
})
