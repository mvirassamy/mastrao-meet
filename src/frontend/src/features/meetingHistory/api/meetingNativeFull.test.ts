import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import {
  fetchNativeFull,
  MeetingNativeFullContractError,
  nativeFullPreviewRequest,
  normalizeNativeFull,
  normalizeNativeFullPreviewSnapshot,
} from './meetingNativeFull'
import {
  fullCoverage,
  fullDigest,
  fullEpochA,
  fullEpochB,
  fullFailedEpoch,
  fullMeetingRef,
  fullPreview,
  fullRequest,
  fullResponse,
  fullSegment,
  fullSnapshot,
  fullHistoryDetail,
} from './meetingNativeFull.fixtures'

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
beforeEach(() => {
  vi.mocked(fetchApi).mockReset()
})
const normalize = (value: unknown) => normalizeNativeFull(value, fullRequest())

describe('retained native full consultation normalization', () => {
  it('retains more than 1000 segments, independent group order and exact text/attribution', () => {
    const first = Array.from({ length: 1001 }, (_, index) =>
      fullSegment({
        id: `${fullEpochA}_s${index}`,
        start_ms: index * 1000,
        speaker: '  Camille  ',
        text: `  Passage ${index}.\n`,
      })
    )
    const second = fullSegment({
      id: `${fullEpochB}_s1`,
      start_ms: 0,
      source_epoch: { epoch_ref: fullEpochB, time_basis: 'source_relative_ms' },
    })
    const raw = fullResponse({ segments: [...first, second] })
    const before = JSON.stringify(raw)
    const result = normalize(raw)
    expect(result?.segments).toHaveLength(1002)
    expect(result?.segments[1000]).toMatchObject({
      text: first[1000].text,
      startMs: 1_000_000,
      speaker: '  Camille  ',
    })
    expect(result?.segments[1001]).toMatchObject({
      text: second.text,
      startMs: 0,
      sourceEpoch: { epochRef: fullEpochB, timeBasis: 'source_relative_ms' },
    })
    expect(result?.coverage.epochs[2]).toEqual({
      epochRef: fullFailedEpoch,
      state: 'failed',
    })
    expect(JSON.stringify(raw)).toBe(before)
    expect(result).not.toHaveProperty('projection')
  })

  it.each([undefined, null, [], 'full', 1])(
    'rejects missing or non-object responses: %j',
    (value) => expect(normalize(value)).toBeUndefined()
  )

  it.each([
    { version: 2 },
    { meeting_ref: 'meeting_other0123456789' },
    { snapshot_digest: 'f'.repeat(64) },
    { coverage: 'complete' },
    { ordering: 'meeting_time' },
    { truncated: true },
    { truncated: undefined },
    { retention_expires_at: 0 },
    { retention_expires_at: Date.now() - 0.5 },
    { retention_expires_at: Number.MAX_SAFE_INTEGER + 1 },
    { retention_expires_at: Number.POSITIVE_INFINITY },
    { retention_expires_at: '9999999999' },
    { segments: [] },
    { segments: null },
    { source: {} },
    { native_coverage: null },
    { native_coverage: fullCoverage({ snapshot_digest: 'f'.repeat(64) }) },
    {
      native_coverage: fullCoverage({ meeting_ref: 'meeting_other0123456789' }),
    },
    {
      native_coverage: fullCoverage({
        epochs: [{ epoch_ref: fullEpochA, state: 'pending' }],
      }),
    },
    {
      native_coverage: fullCoverage({
        epochs: [{ epoch_ref: fullEpochA, state: 'available' }],
      }),
    },
  ])(
    'rejects mismatched, malformed or non-final consultation envelopes: %j',
    (changes) => expect(normalize(fullResponse(changes))).toBeUndefined()
  )

  it('rejects expired retention exactly at the boundary in seconds, rather than milliseconds', () => {
    const now = Date.now()
    expect(
      normalizeNativeFull(
        fullResponse({ retention_expires_at: Math.floor(now / 1000) }),
        fullRequest(),
        now
      )
    ).toBeUndefined()
    expect(
      normalizeNativeFull(
        fullResponse({ retention_expires_at: Math.floor(now / 1000) + 1 }),
        fullRequest(),
        now
      )
    ).toBeDefined()
  })

  it.each([
    null,
    {},
    fullSegment({ id: 's1' }),
    fullSegment({ id: `${fullEpochB}_s1` }),
    fullSegment({ id: `${fullEpochA}_${'x'.repeat(161)}` }),
    fullSegment({ start_ms: -1 }),
    fullSegment({ start_ms: 1.5 }),
    fullSegment({ start_ms: Number.NaN }),
    fullSegment({ start_ms: Number.MAX_SAFE_INTEGER + 1 }),
    fullSegment({ speaker: null }),
    fullSegment({ speaker: 'x'.repeat(161) }),
    fullSegment({ text: '' }),
    fullSegment({ text: 'x'.repeat(4001) }),
    fullSegment({ source_epoch: undefined }),
    fullSegment({
      source_epoch: {
        epoch_ref: fullEpochA,
        time_basis: 'meeting_relative_ms',
      },
    }),
    fullSegment({
      source_epoch: {
        epoch_ref: fullFailedEpoch,
        time_basis: 'source_relative_ms',
      },
    }),
    fullSegment({
      source_epoch: {
        epoch_ref: fullEpochA,
        time_basis: 'source_relative_ms',
        aligned: true,
      },
    }),
    fullSegment({ canonical_revision: 3 }),
  ])('withdraws the whole result on an invalid segment: %j', (segment) =>
    expect(normalize(fullResponse({ segments: [segment] }))).toBeUndefined()
  )

  it('rejects duplicates, reopened groups and unsorted local clocks without reordering', () => {
    expect(
      normalize(fullResponse({ segments: [fullSegment(), fullSegment()] }))
    ).toBeUndefined()
    const first = fullSegment()
    const second = fullResponse().segments[1]
    const next = fullSegment({ id: `${fullEpochA}_s2`, start_ms: 6000 })
    expect(
      normalize(fullResponse({ segments: [first, second, next] }))
    ).toBeUndefined()
    expect(normalize(fullResponse({ segments: [next, first] }))).toBeUndefined()
  })

  it('accepts 8192 minimal segments and refuses overflow without truncation', () => {
    const segments = Array.from({ length: 8192 }, (_, index) =>
      fullSegment({
        id: `${fullEpochA}_s${index}`,
        start_ms: index,
        text: 'x',
        speaker: '',
      })
    )
    expect(normalize(fullResponse({ segments }))?.segments).toHaveLength(8192)
    expect(
      normalize(
        fullResponse({
          segments: [
            ...segments,
            fullSegment({ id: `${fullEpochA}_overflow` }),
          ],
        })
      )
    ).toBeUndefined()
  })

  it('enforces text and flattened JSON budgets independently, including UTF-8 bytes', () => {
    const textOverflow = Array.from({ length: 99 }, (_, index) =>
      fullSegment({
        id: `${fullEpochA}_s${index}`,
        start_ms: index,
        text: 'é'.repeat(4000),
      })
    )
    expect(normalize(fullResponse({ segments: textOverflow }))).toBeUndefined()
    const transportOverflow = Array.from({ length: 8192 }, (_, index) =>
      fullSegment({
        id: `${fullEpochA}_${String(index).padStart(160, 'x')}`,
        start_ms: index,
        text: 'x',
        speaker: 'x'.repeat(160),
      })
    )
    expect(
      normalize(fullResponse({ segments: transportOverflow }))
    ).toBeUndefined()
  })
})

describe('native full preview eligibility and fresh identity', () => {
  it('keeps the wire digest only when fresh native coverage validates', () => {
    expect(
      normalizeNativeFullPreviewSnapshot(
        fullCoverage(),
        fullHistoryDetail().transcript,
        fullMeetingRef,
        fullPreview().projection
      )
    ).toEqual({
      meetingRef: fullMeetingRef,
      snapshotDigest: fullDigest,
      previewIdentity: expect.any(String),
      projectionRevision: 1,
    })
    for (const raw of [
      undefined,
      null,
      fullCoverage({ snapshot_digest: null }),
      fullCoverage({ meeting_ref: 'meeting_other0123456789' }),
      fullCoverage({ epochs: null }),
    ]) {
      expect(
        normalizeNativeFullPreviewSnapshot(
          raw,
          fullHistoryDetail().transcript,
          fullMeetingRef,
          fullPreview().projection
        )
      ).toBeUndefined()
    }
  })

  it('binds eligibility to the fresh projection, while an older canonical body stays immutable', () => {
    const preview = fullPreview()
    const pending = {
      ...preview,
      projection: {
        ...preview.projection,
        digest: 'f'.repeat(64),
        refreshPending: true,
      },
    }
    expect(
      nativeFullPreviewRequest(fullMeetingRef, pending, fullSnapshot())
    ).toEqual(fullRequest())
    for (const projection of [
      { ...preview.projection, digest: 'f'.repeat(64) },
      { ...preview.projection, state: 'unknown' as const },
      { ...preview.projection, refreshPending: true },
      { ...preview.projection, revision: -1 },
      { ...preview.projection, revision: 0.5 },
    ]) {
      expect(
        normalizeNativeFullPreviewSnapshot(
          fullCoverage(),
          fullHistoryDetail().transcript,
          fullMeetingRef,
          projection
        )
      ).toBeUndefined()
    }
  })

  it('requires a settled native truncated body bound to this fresh meeting/digest', () => {
    expect(
      nativeFullPreviewRequest(fullMeetingRef, fullPreview(), fullSnapshot())
    ).toEqual(fullRequest())
    for (const changes of [
      { truncated: false },
      { segments: [] },
      { nativeCoverage: undefined },
      { status: 'transcribing' as const },
      { projection: { ...fullPreview().projection, revision: 2 } },
      { projection: { ...fullPreview().projection, pendingRevision: 2 } },
      {
        segments: [
          {
            id: 'provider',
            startMs: 0,
            speaker: 'Provider',
            text: 'Provider.',
          },
        ],
      },
      {
        segments: [
          ...fullPreview().segments,
          {
            id: 'provider',
            startMs: 0,
            speaker: 'Provider',
            text: 'Provider.',
          },
        ],
      },
      {
        nativeCoverage: {
          ...fullRequest().coverage,
          epochs: [{ epochRef: fullEpochA, state: 'pending' as const }],
        },
      },
    ])
      expect(
        nativeFullPreviewRequest(
          fullMeetingRef,
          { ...fullPreview(), ...changes },
          fullSnapshot()
        )
      ).toBeUndefined()
    expect(
      nativeFullPreviewRequest(fullMeetingRef, fullPreview(), undefined)
    ).toBeUndefined()
    expect(
      nativeFullPreviewRequest(
        'meeting_other0123456789',
        fullPreview(),
        fullSnapshot()
      )
    ).toBeUndefined()
  })
})

describe('native full transport', () => {
  it('uses only an explicit GET consultation with the expected digest and no-store', async () => {
    const response = fullResponse()
    vi.mocked(fetchApi).mockResolvedValue(response)
    const controller = new AbortController()
    const result = await fetchNativeFull(fullRequest(), controller.signal)
    expect(result.segments[0].text).toBe(response.segments[0].text)
    expect(fetchApi).toHaveBeenCalledExactlyOnceWith(
      `meetings/history/${fullMeetingRef}/native-transcript/?snapshot_digest=${fullDigest}`,
      { cache: 'no-store', signal: controller.signal }
    )
  })

  it('rejects invalid responses and forwards HTTP refusals without a fallback request', async () => {
    vi.mocked(fetchApi).mockResolvedValue(fullResponse({ truncated: true }))
    await expect(fetchNativeFull(fullRequest())).rejects.toBeInstanceOf(
      MeetingNativeFullContractError
    )
    const refusal = new Error('409')
    vi.mocked(fetchApi).mockRejectedValue(refusal)
    await expect(fetchNativeFull(fullRequest())).rejects.toBe(refusal)
    expect(fetchApi).toHaveBeenCalledTimes(2)
  })

  it('rejects an invalid identity before transport', async () => {
    await expect(
      fetchNativeFull({ ...fullRequest(), snapshotDigest: 'invalid' })
    ).rejects.toBeInstanceOf(MeetingNativeFullContractError)
    expect(fetchApi).not.toHaveBeenCalled()
  })
})

it.each(['epoch_limit_exceeded', 'result_bytes_limit_exceeded'] as const)(
  'refuses every full consultation entry point with %s',
  async (reason) => {
    const rawCoverage = fullCoverage({
      epochs_unavailable_reason: reason,
      epochs: [],
    })
    const request = fullRequest()
    request.coverage = { ...request.coverage, epochsUnavailableReason: reason }
    expect(
      nativeFullPreviewRequest(
        fullMeetingRef,
        { ...fullPreview(), nativeCoverage: request.coverage },
        fullSnapshot()
      )
    ).toBeUndefined()
    expect(
      normalizeNativeFullPreviewSnapshot(
        rawCoverage,
        fullHistoryDetail().transcript,
        fullMeetingRef,
        fullPreview().projection
      )
    ).toBeUndefined()
    expect(normalizeNativeFull(fullResponse(), request)).toBeUndefined()
    expect(
      normalize(fullResponse({ native_coverage: rawCoverage }))
    ).toBeUndefined()
    await expect(fetchNativeFull(request)).rejects.toBeInstanceOf(
      MeetingNativeFullContractError
    )
    expect(fetchApi).not.toHaveBeenCalled()
  }
)
