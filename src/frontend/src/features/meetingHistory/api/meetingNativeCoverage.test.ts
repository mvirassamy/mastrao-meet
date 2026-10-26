import { describe, expect, it } from 'vitest'
import { mergeHistoryDetail, normalizeHistoryDetail } from './meetingHistoryApi'
import { normalizeNativeCoverage } from './meetingNativeCoverage'
import { nativeDomainFixture } from './meetingNativeCaptureDomain.fixtures'

const meetingRef = 'meeting_native0123456789'
const uuid = (index: number) =>
  `${index.toString(16).padStart(8, '0')}-1234-4567-89ab-123456789abc`
const epochRef = uuid(1)
const observationRef = uuid(100)

const snapshot = (changes: Record<string, unknown> = {}) => ({
  version: 1,
  meeting_ref: meetingRef,
  snapshot_digest: 'a'.repeat(64),
  track_observations: {
    meeting_coverage: 'unverified',
    truncated: false,
    tracks: [
      {
        observation_ref: observationRef,
        capture_state: 'admitted',
        epoch_refs: [epochRef],
      },
    ],
  },
  epochs: [{ epoch_ref: epochRef, state: 'available' }],
  ...changes,
})

const withTracks = (tracks: unknown, changes: Record<string, unknown> = {}) =>
  snapshot({
    track_observations: {
      meeting_coverage: 'unverified',
      truncated: false,
      tracks,
      ...changes,
    },
  })

const normalize = (value: unknown) => normalizeNativeCoverage(value, meetingRef)

describe('native coverage normalization', () => {
  it('retains source states and links without claiming complete coverage', () => {
    const raw = snapshot({
      epochs: [
        { epoch_ref: uuid(3), state: 'failed' },
        { epoch_ref: epochRef, state: 'available' },
        { epoch_ref: uuid(2), state: 'pending' },
      ],
    })
    const before = JSON.stringify(raw)
    expect(normalize(raw)).toEqual({
      meetingCoverage: 'unverified',
      observationsAvailable: true,
      truncated: false,
      tracks: [
        {
          observationRef,
          captureState: 'admitted',
          epochRefs: [epochRef],
        },
      ],
      epochs: [
        { epochRef: uuid(3), state: 'failed' },
        { epochRef, state: 'available' },
        { epochRef: uuid(2), state: 'pending' },
      ],
    })
    expect(JSON.stringify(raw)).toBe(before)
  })

  it('distinguishes unavailable observations from a known empty list', () => {
    expect(
      normalize(snapshot({ track_observations: undefined }))
    ).toMatchObject({
      observationsAvailable: false,
      meetingCoverage: 'unverified',
      truncated: false,
      tracks: [],
    })
    expect(normalize(withTracks([]))).toMatchObject({
      observationsAvailable: true,
      tracks: [],
    })
    expect(normalize(withTracks([], { truncated: true }))?.truncated).toBe(true)
  })

  it('preserves absent legacy links, explicit empty links and repeated publications', () => {
    const tracks = [
      { observation_ref: uuid(100), capture_state: 'admitted' },
      {
        observation_ref: uuid(101),
        capture_state: 'unadmitted',
        epoch_refs: [],
      },
      { observation_ref: uuid(102), capture_state: 'admitted', epoch_refs: [] },
      {
        observation_ref: uuid(103),
        capture_state: 'admitted',
        epoch_refs: [epochRef],
      },
      {
        observation_ref: uuid(104),
        capture_state: 'admitted',
        epoch_refs: [epochRef],
      },
    ]
    const result = normalize(withTracks(tracks))
    expect(result?.tracks).toHaveLength(5)
    expect(result?.tracks[0]).not.toHaveProperty('epochRefs')
    expect(result?.tracks[1].epochRefs).toEqual([])
    expect(result?.tracks[2].epochRefs).toEqual([])
    expect(result?.tracks.slice(3).map((track) => track.epochRefs)).toEqual([
      [epochRef],
      [epochRef],
    ])
  })

  it.each([undefined, null, [], 1, 'coverage'])(
    'rejects missing or non-object snapshots: %j',
    (value) => expect(normalize(value)).toBeUndefined()
  )

  it.each([
    { version: 2 },
    { version: true },
    { meeting_ref: 'meeting_other0123456789' },
    { meeting_ref: '../unsafe' },
    { meeting_ref: `${meetingRef} ` },
    { snapshot_digest: 'A'.repeat(64) },
    { snapshot_digest: 'a'.repeat(63) },
    { snapshot_digest: null },
    { track_observations: null },
    { epochs: null },
    { epochs: {} },
    { epochs: [{ epoch_ref: epochRef, state: 'complete' }] },
    { epochs: [{ epoch_ref: epochRef, state: 'available', text: 'hidden' }] },
    { epochs: [{ epoch_ref: 'invalid', state: 'available' }] },
    {
      epochs: [
        {
          epoch_ref: '12345678-1234-0567-89ab-123456789abc',
          state: 'available',
        },
      ],
    },
    { epochs: [{ epoch_ref: epochRef }] },
    { epochs: [null] },
    { complete: true },
  ])('rejects invalid snapshot fields: %j', (changes) => {
    expect(normalize(snapshot(changes))).toBeUndefined()
  })

  it.each([
    { meeting_coverage: 'complete' },
    { truncated: 'false' },
    { truncated: undefined },
    { tracks: {} },
    { aligned: true },
  ])('rejects invalid observation metadata: %j', (changes) => {
    expect(normalize(withTracks([], changes))).toBeUndefined()
  })

  it.each([
    null,
    { observation_ref: 'invalid', capture_state: 'admitted' },
    { observation_ref: observationRef, capture_state: 'available' },
    {
      observation_ref: observationRef,
      capture_state: 'admitted',
      epoch_refs: null,
    },
    {
      observation_ref: observationRef,
      capture_state: 'admitted',
      epoch_refs: ['invalid'],
    },
    {
      observation_ref: observationRef,
      capture_state: 'admitted',
      epoch_refs: [uuid(2)],
    },
    {
      observation_ref: observationRef,
      capture_state: 'unadmitted',
      epoch_refs: [epochRef],
    },
    {
      observation_ref: observationRef,
      capture_state: 'admitted',
      aligned: true,
    },
  ])('rejects invalid or foreign track links: %j', (track) => {
    expect(normalize(withTracks([track]))).toBeUndefined()
  })

  it('rejects duplicate epochs, observations and links within one observation', () => {
    expect(
      normalize(
        snapshot({
          epochs: [
            { epoch_ref: epochRef, state: 'available' },
            { epoch_ref: epochRef, state: 'failed' },
          ],
        })
      )
    ).toBeUndefined()
    const track = snapshot().track_observations.tracks[0]
    expect(normalize(withTracks([track, track]))).toBeUndefined()
    expect(
      normalize(withTracks([{ ...track, epoch_refs: [epochRef, epochRef] }]))
    ).toBeUndefined()
  })

  it('accepts exact bounds and rejects overflow without silently truncating', () => {
    const epochs = Array.from({ length: 32 }, (_, index) => ({
      epoch_ref: uuid(index + 1),
      state: 'available',
    }))
    const tracks = Array.from({ length: 128 }, (_, index) => ({
      observation_ref: uuid(index + 100),
      capture_state: 'admitted',
      epoch_refs: epochs.map((epoch) => epoch.epoch_ref),
    }))
    const bounded = { ...withTracks(tracks), epochs }
    expect(normalize(bounded)?.tracks).toHaveLength(128)
    expect(normalize(bounded)?.epochs).toHaveLength(32)
    expect(
      normalize({
        ...bounded,
        epochs: [...epochs, { epoch_ref: uuid(33), state: 'pending' }],
      })
    ).toBeUndefined()
    expect(
      normalize({
        ...withTracks([
          ...tracks,
          { ...tracks[0], observation_ref: uuid(999) },
        ]),
        epochs,
      })
    ).toBeUndefined()
    expect(
      normalize({
        ...withTracks([
          {
            ...tracks[0],
            epoch_refs: Array.from({ length: 33 }, (_, index) =>
              uuid(index + 1)
            ),
          },
        ]),
        epochs,
      })
    ).toBeUndefined()
  })
})

const detail = (changes: Record<string, unknown> = {}) => {
  const normalized = normalizeHistoryDetail({
    id: meetingRef,
    started_at: '2026-10-25T10:00:00Z',
    transcript_projection: {
      version: 1,
      state: 'available',
      revision: 5,
      digest: 'b'.repeat(64),
      source: null,
    },
    transcript: {
      segments: [{ id: 's1', start_ms: 0, text: 'Canonical retained.' }],
    },
    native_coverage: snapshot(),
    ...changes,
  })
  if (!normalized) throw new Error('Expected valid history fixture')
  return normalized
}

describe('native coverage in history refresh', () => {
  it('does not change canonical readiness or text when coverage is available', () => {
    const response = detail({
      transcript_projection: { version: 1, state: 'failed', revision: 1 },
    })
    expect(response.transcript.nativeCoverage?.epochs[0].state).toBe(
      'available'
    )
    expect(response.transcriptStatus).toBe('failed')
    expect(response.transcript.projection.state).toBe('failed')
    expect(response.transcript.segments).toEqual([])
    expect(response.summaryStatus).toBe('unknown')
  })

  it.each([
    undefined,
    null,
    snapshot({ meeting_ref: 'meeting_other0123456789' }),
    snapshot({ epochs: null }),
  ])(
    'clears withdrawn or invalid coverage while keeping a newer canonical revision: %j',
    (coverage) => {
      const cached = detail()
      const incoming = detail({
        transcript_projection: { version: 1, state: 'unknown', revision: 4 },
        transcript: null,
        native_coverage: coverage,
      })
      expect(cached.transcript.nativeCoverage).toBeDefined()
      expect(incoming.transcript.nativeCoverage).toBeUndefined()
      const merged = mergeHistoryDetail(cached, incoming)
      expect(merged.transcript.nativeCoverage).toBeUndefined()
      expect(merged.transcriptProjection.revision).toBe(5)
      expect(merged.transcript.segments[0].text).toBe('Canonical retained.')
      expect(
        mergeHistoryDetail(merged, incoming).transcript.nativeCoverage
      ).toBeUndefined()
    }
  )

  it('replaces coverage independently of canonical revision and clears old observations', () => {
    const incoming = detail({
      transcript_projection: { version: 1, state: 'unknown', revision: 4 },
      native_coverage: snapshot({
        snapshot_digest: 'c'.repeat(64),
        track_observations: undefined,
        epochs: [{ epoch_ref: epochRef, state: 'failed' }],
      }),
    })
    const merged = mergeHistoryDetail(detail(), incoming)
    expect(merged.transcript.nativeCoverage).toEqual(
      incoming.transcript.nativeCoverage
    )
    expect(merged.transcript.nativeCoverage?.observationsAvailable).toBe(false)
    expect(merged.transcript.nativeCoverage?.tracks).toEqual([])
    expect(merged.transcriptProjection.revision).toBe(5)
  })
})

describe('native consultation read limits', () => {
  it.each(['epoch_limit_exceeded', 'result_bytes_limit_exceeded'] as const)(
    'retains %s with absent, null or invalidated domain',
    (reason) => {
      for (const domain of [
        undefined,
        null,
        nativeDomainFixture({
          closure_state: 'unknown',
          reason: 'epoch_limit_exceeded',
          counts: null,
        }),
      ]) {
        for (const observations of [
          undefined,
          { meeting_coverage: 'unverified', truncated: false, tracks: [] },
        ]) {
          const result = normalize(
            snapshot({
              epochs_unavailable_reason: reason,
              epochs: [],
              track_observations: observations,
              capture_domain: domain,
            })
          )
          expect(result).toMatchObject({
            epochsUnavailableReason: reason,
            epochs: [],
            tracks: [],
          })
          if (domain)
            expect(result?.captureDomain).toMatchObject({
              closureState: 'unknown',
              counts: null,
            })
        }
      }
    }
  )

  it.each([
    undefined,
    null,
    '',
    'payload_limit_exceeded',
    'epoch_limit_exceeded ',
    1,
    {},
    [],
  ])('rejects a present invalid marker: %j', (reason) => {
    expect(
      normalize(
        snapshot({
          epochs_unavailable_reason: reason,
          epochs: [],
          track_observations: undefined,
        })
      )
    ).toBeUndefined()
  })

  it('rejects contradictory nonempty epochs and observations', () => {
    const reason = { epochs_unavailable_reason: 'epoch_limit_exceeded' }
    expect(normalize(snapshot(reason))).toBeUndefined()
    expect(normalize(snapshot({ ...reason, epochs: [] }))).toBeUndefined()
    expect(
      normalize(
        snapshot({
          ...reason,
          epochs: [],
          track_observations: {
            meeting_coverage: 'unverified',
            truncated: false,
            tracks: [
              {
                observation_ref: observationRef,
                capture_state: 'unadmitted',
                epoch_refs: [],
              },
            ],
          },
        })
      )
    ).toBeUndefined()
    expect(
      normalize(
        snapshot({
          ...reason,
          epochs: [],
          track_observations: {
            meeting_coverage: 'unverified',
            truncated: true,
            tracks: [],
          },
        })
      )?.epochsUnavailableReason
    ).toBe('epoch_limit_exceeded')
  })
})
