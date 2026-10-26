import type { MeetingLiveEvent } from './types'

export const liveMeetingRef = 'meeting_0123456789abcdef'
export const liveSourceRef = 'source_0123456789abcdef'
export const liveLegRef = 'leg_0123456789abcdef'

export const liveFinal = (changes: Record<string, unknown> = {}) => ({
  ...({
    version: 2,
    kind: 'final',
    source_ref: liveSourceRef,
    leg_ref: liveLegRef,
    sequence: 3,
    item_ref: 'item-1',
    revision: 2,
    text: '  De\u0301cision : ne pas payer 250 €.\n',
    language: null,
    timing: {
      state: 'source_buffer',
      sample_rate: 24000,
      start_sample: 120000,
      end_sample: 144000,
    },
  } satisfies MeetingLiveEvent),
  ...changes,
})

export const liveSource = (
  events: unknown[] = [liveFinal()],
  changes: Record<string, unknown> = {}
) => ({
  source_ref: liveSourceRef,
  participant_kind: 'host',
  participant_ref: 'participant_0123456789',
  state: 'ended',
  incomplete: true,
  confirmed_sequence: 10,
  events,
  ...changes,
})

export const liveSnapshot = (sources: unknown[] = [liveSource()]) => ({
  version: 1,
  meeting_ref: liveMeetingRef,
  matter_ref: 'matter_0123456789abcdef',
  coverage: 'partial_sources',
  meeting_coverage: 'unverified',
  truncated: false,
  sources,
})

export const liveDetail = (changes: Record<string, unknown> = {}) => ({
  id: liveMeetingRef,
  started_at: '2026-10-25T10:00:00Z',
  summary_status: 'absent',
  transcript_status: 'failed',
  live_transcript: liveSnapshot(),
  ...changes,
})
