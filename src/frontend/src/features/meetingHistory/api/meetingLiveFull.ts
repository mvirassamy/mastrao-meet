import { fetchApi } from '@/api/fetchApi'
import { normalizeLiveTranscriptValue } from './meetingLiveTranscript'
import type { MeetingLiveSource, MeetingTranscript } from './types'

const REFERENCE = /^[A-Za-z0-9_-]{16,160}$/u
const DIGEST = /^[a-f0-9]{64}$/u

export type LiveFullSnapshot = {
  meetingRef: string
  matterRef: string
  sourceHeadsDigest: string
  previewIdentity: string
}
export type LiveFullRequest = LiveFullSnapshot & {
  sources: MeetingLiveSource[]
}
export type MeetingLiveFull = {
  sources: MeetingLiveSource[]
  snapshotDigest: string
  retentionExpiresAt: number
}

/** Compare public heads in canonical source order without claiming a text hash. */
const sourceHeads = (sources: MeetingLiveSource[]) =>
  sources.map((source) => ({
    source_ref: source.sourceRef,
    participant_kind: source.participantKind,
    participant_ref: source.participantRef,
    state: source.state,
    incomplete: source.incomplete,
    confirmed_sequence: source.confirmedSequence,
  }))

export const liveFullPreviewRequest = (
  meetingRef: string,
  transcript: MeetingTranscript
): LiveFullRequest | undefined => {
  const snapshot = transcript.liveFullSnapshot
  if (
    !snapshot ||
    snapshot.meetingRef !== meetingRef ||
    !REFERENCE.test(meetingRef) ||
    !REFERENCE.test(snapshot.matterRef) ||
    !DIGEST.test(snapshot.sourceHeadsDigest) ||
    !transcript.liveTruncated ||
    !transcript.liveSources?.length
  )
    return undefined
  return { ...snapshot, sources: transcript.liveSources }
}

export const normalizeLiveFull = (
  value: unknown,
  expected: LiveFullRequest,
  now = Date.now()
): MeetingLiveFull | undefined => {
  if (typeof value !== 'object' || value === null || Array.isArray(value))
    return undefined
  const raw = value as Record<string, unknown>
  const fields = [
    'version',
    'meeting_ref',
    'matter_ref',
    'source_heads_digest',
    'snapshot_digest',
    'coverage',
    'meeting_coverage',
    'ordering',
    'retention_expires_at',
    'truncated',
    'sources',
  ]
  if (
    Object.keys(raw).length !== fields.length ||
    !fields.every((field) => Object.hasOwn(raw, field)) ||
    raw.matter_ref !== expected.matterRef ||
    raw.source_heads_digest !== expected.sourceHeadsDigest ||
    typeof raw.snapshot_digest !== 'string' ||
    !DIGEST.test(raw.snapshot_digest) ||
    raw.ordering !== 'independent_source_groups' ||
    raw.truncated !== false ||
    typeof raw.retention_expires_at !== 'number' ||
    !Number.isSafeInteger(raw.retention_expires_at) ||
    raw.retention_expires_at <= 0 ||
    raw.retention_expires_at <= now / 1000
  )
    return undefined
  const full = normalizeLiveTranscriptValue(
    raw,
    expected.meetingRef,
    'retained'
  )
  if (
    !full.liveSources ||
    JSON.stringify(sourceHeads(full.liveSources)) !==
      JSON.stringify(sourceHeads(expected.sources))
  )
    return undefined
  return {
    sources: full.liveSources,
    snapshotDigest: raw.snapshot_digest,
    retentionExpiresAt: raw.retention_expires_at,
  }
}

export class MeetingLiveFullContractError extends Error {
  constructor() {
    super('Invalid retained live transcript consultation')
  }
}

export const fetchLiveFull = async (
  expected: LiveFullRequest,
  signal?: AbortSignal
): Promise<MeetingLiveFull> => {
  if (
    !REFERENCE.test(expected.meetingRef) ||
    !REFERENCE.test(expected.matterRef) ||
    !DIGEST.test(expected.sourceHeadsDigest) ||
    !expected.sources.length
  )
    throw new MeetingLiveFullContractError()
  const raw = await fetchApi<unknown>(
    `meetings/history/${encodeURIComponent(expected.meetingRef)}/live-transcript/?source_heads_digest=${expected.sourceHeadsDigest}`,
    { cache: 'no-store', signal }
  )
  const full = normalizeLiveFull(raw, expected)
  if (!full) throw new MeetingLiveFullContractError()
  return full
}
