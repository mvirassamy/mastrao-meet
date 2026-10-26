import {
  liveDetail,
  liveFinal,
  liveSnapshot,
  liveSource,
  liveLegRef,
  liveSourceRef,
} from './meetingLiveTranscript.fixtures'

export const liveHeadsDigest = 'a'.repeat(64)
export const liveFullSource = (
  events: unknown[] = [liveFinal()],
  changes: Record<string, unknown> = {}
) => liveSource(events, { confirmed_sequence: 100_000, ...changes })
export const liveFullPreview = (changes: Record<string, unknown> = {}) => ({
  ...liveSnapshot([liveFullSource()]),
  source_heads_digest: liveHeadsDigest,
  truncated: true,
  ...changes,
})
export const liveFullDetail = (changes: Record<string, unknown> = {}) =>
  liveDetail({
    summary_status: 'available',
    summary: { paragraphs: ['Synthèse existante.'] },
    transcript_projection: {
      version: 1,
      state: 'available',
      revision: 5,
      digest: 'd'.repeat(64),
      source: null,
    },
    transcript: {
      status: 'available',
      segments: [
        { id: 'canonical', text: 'Canonique prioritaire.', start_ms: 5000 },
      ],
    },
    live_transcript: liveFullPreview(),
    ...changes,
  })
export const liveMarker = (kind: 'gap' | 'end') => ({
  version: 2,
  source_ref: liveSourceRef,
  leg_ref: liveLegRef,
  sequence: kind === 'gap' ? 9000 : 9001,
  kind,
  reason: 'capture_incomplete',
  ...(kind === 'end' ? { final_event_count: 2000 } : {}),
})
export const liveFullResponse = (changes: Record<string, unknown> = {}) => ({
  ...liveFullPreview(),
  snapshot_digest: 'b'.repeat(64),
  ordering: 'independent_source_groups',
  retention_expires_at: 2_000_000_000,
  truncated: false,
  sources: [
    liveFullSource([
      liveFinal({ text: '  De\u0301cision exacte.\n' }),
      liveMarker('gap'),
      liveMarker('end'),
    ]),
  ],
  ...changes,
})
