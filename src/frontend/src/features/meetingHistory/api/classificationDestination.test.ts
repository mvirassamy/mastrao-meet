import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import {
  readClassification,
  type MeetingClassification,
} from '@/features/meeting-classification/classificationApi'
import { classificationMatterUrl } from '@/features/meeting-classification/classificationMatterUrl'

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
const classification: MeetingClassification = {
  version: 1,
  meeting_ref: 'meeting_0123456789abcdef',
  matter_ref: 'matter_0123456789abcdef',
  matter_display_label: 'Dossier autorisé',
  matter_path: '/orgs/server-tenant/matters/matter_0123456789abcdef',
  classification_version: 4,
  can_classify: false,
}
const api = vi.mocked(fetchApi)
beforeEach(() => {
  api.mockReset()
})

function read() {
  return readClassification(
    classification.meeting_ref,
    new AbortController().signal
  )
}

describe('authorized classification destination', () => {
  it('reads the public bridge projection and joins the exact server path to the configured origin', async () => {
    api.mockResolvedValue(classification)
    const result = await read()
    expect(api.mock.calls[0][0]).toBe(
      `meetings/history/${classification.meeting_ref}/classification/`
    )
    expect(api.mock.calls[0][1]?.cache).toBe('no-store')
    expect(classificationMatterUrl(result, 'https://platform.example/')).toBe(
      `https://platform.example${classification.matter_path}`
    )
    expect(classificationMatterUrl(result, 'http://localhost:3077/')).toBe(
      `http://localhost:3077${classification.matter_path}`
    )
  })

  it.each([
    'https://foreign.example/orgs/server-tenant/matters/matter_0123456789abcdef',
    '//foreign.example/orgs/server-tenant/matters/matter_0123456789abcdef',
    'javascript:alert(1)',
    '/orgs/server-tenant/matters/matter_other0123456789',
    '/orgs/server-tenant/matters/matter_0123456789abcdef?token=private',
    '/orgs/server-tenant/matters/matter_0123456789abcdef#fragment',
    '/orgs/server-tenant/../matters/matter_0123456789abcdef',
    `/orgs/${'a'.repeat(101)}/matters/matter_0123456789abcdef`,
    '',
    undefined,
  ])(
    'rejects a destination outside the exact published path contract: %s',
    async (matter_path) => {
      api.mockResolvedValue({ ...classification, matter_path })
      await expect(read()).rejects.toThrow('classification destination path')
    }
  )

  it('withholds opening when the current projection has no readable destination', async () => {
    api.mockResolvedValue({
      ...classification,
      matter_display_label: null,
      matter_path: null,
    })
    const result = await read()
    expect(
      classificationMatterUrl(result, 'https://platform.example')
    ).toBeNull()
  })

  it.each([
    { matter_display_label: null },
    { matter_ref: null },
    { matter_path: null },
  ])(
    'rejects a path or readable label detached from the canonical folder: %s',
    async (changes) => {
      api.mockResolvedValue({ ...classification, ...changes })
      await expect(read()).rejects.toThrow('classification destination path')
    }
  )

  it.each([
    undefined,
    '',
    'javascript:alert(1)',
    'http://platform.example',
    'https://user:password@platform.example',
    'https://platform.example/guessed-base',
    'https://platform.example?token=private',
    'https://platform.example#fragment',
  ])(
    'withholds opening without a valid configured Platform origin: %s',
    (origin) => {
      expect(classificationMatterUrl(classification, origin)).toBeNull()
    }
  )
})
