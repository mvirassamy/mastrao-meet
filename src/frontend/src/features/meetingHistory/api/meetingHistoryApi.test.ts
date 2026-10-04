import { describe, expect, it } from 'vitest'
import {
  mergeContentProjection,
  mergeHistoryDetail,
  normalizeContentStatus,
  normalizeHistoryDetail,
  normalizeHistoryPage,
} from './meetingHistoryApi'

describe('normalizeHistoryPage', () => {
  it('keeps valid meetings, most recent first, and drops malformed rows', () => {
    const page = normalizeHistoryPage({
      results: [
        {
          id: 'older',
          title: 'Older',
          started_at: '2026-09-01T10:00:00Z',
          summary_status: 'available',
          transcript_status: 'processing',
        },
        { id: 'no-date', title: 'Broken' },
        {
          id: 'newer',
          title: '  ',
          started_at: '2026-09-20T10:00:00Z',
          ended_at: '2026-09-20T10:30:00Z',
          participant_count: 3,
          participant_names: ['Camille Martin', ' ', 42, 'Karim'],
          summary_status: 'something-new',
          transcript_status: 'failed',
        },
      ],
      next_cursor: 'next',
    })

    expect(page.items.map((item) => item.id)).toEqual(['newer', 'older'])
    expect(page.items[0]).toMatchObject({
      title: null,
      participantCount: 3,
      participantNames: ['Camille Martin', 'Karim'],
      summaryStatus: 'unknown',
      transcriptStatus: 'failed',
    })
    expect(page.nextCursor).toBe('next')
  })
})

describe('normalizeContentStatus', () => {
  it('never turns an unknown status into available content', () => {
    expect(normalizeContentStatus('available')).toBe('available')
    expect(normalizeContentStatus(undefined)).toBe('unknown')
    expect(normalizeContentStatus('ready')).toBe('unknown')
  })

  it('replaces revision and digest atomically for a newer readable result', () => {
    const digestA = 'a'.repeat(64)
    const digestB = 'b'.repeat(64)
    const current = {
      version: 1,
      state: 'available',
      revision: 1,
      digest: digestA,
      source: { revision: 1, digest: digestA },
    } as const
    const incoming = {
      version: 1,
      state: 'available',
      revision: 2,
      digest: digestB,
      source: { revision: 2, digest: digestB },
    } as const

    expect(mergeContentProjection(current, incoming)).toEqual(incoming)
  })

  it('accepts newer non-readable metadata instead of pinning old content', () => {
    const digest = 'a'.repeat(64)
    const current = normalizeHistoryDetail({
      id: 'meeting-1',
      started_at: '2026-09-20T10:00:00Z',
      summary_projection: {
        version: 1,
        state: 'available',
        revision: 1,
        digest,
        source: { revision: 1, digest },
      },
      transcript_projection: {
        version: 1,
        state: 'available',
        revision: 1,
        digest,
        source: null,
      },
      summary: { text: 'Outdated summary' },
      transcript: {
        segments: [{ id: 's1', start_ms: 0, text: 'Transcript' }],
      },
    })
    const incoming = normalizeHistoryDetail({
      id: 'meeting-1',
      started_at: '2026-09-20T10:00:00Z',
      summary_projection: {
        version: 1,
        state: 'transcribing',
        revision: 2,
        digest: null,
        source: { revision: 2, digest },
      },
      transcript_projection: {
        version: 1,
        state: 'available',
        revision: 1,
        digest,
        source: null,
      },
      summary: null,
      transcript: {
        segments: [{ id: 's1', start_ms: 0, text: 'Transcript' }],
      },
    })

    if (!current || !incoming) throw new Error('Expected valid meeting details')
    const merged = mergeHistoryDetail(current, incoming)
    expect(merged.summaryStatus).toBe('transcribing')
    expect(merged.summaryProjection.revision).toBe(2)
    expect(merged.summary.paragraphs).toEqual([])
  })

  it('keeps the last readable body while a newer artifact is unavailable', () => {
    const digest = 'a'.repeat(64)
    const current = normalizeHistoryDetail({
      id: 'meeting-1',
      started_at: '2026-09-29T00:00:00Z',
      transcript_projection: {
        version: 1,
        state: 'available',
        revision: 4,
        digest,
        source: null,
      },
      transcript: {
        segments: [{ id: 's1', start_ms: 0, text: 'Contenu conservé' }],
      },
    })
    const incoming = normalizeHistoryDetail({
      id: 'meeting-1',
      started_at: '2026-09-29T00:00:00Z',
      transcript_projection: {
        version: 1,
        state: 'unknown',
        revision: 5,
        digest: 'b'.repeat(64),
        source: null,
      },
      transcript: null,
    })

    if (!current || !incoming) throw new Error('Expected valid meeting details')
    const merged = mergeHistoryDetail(current, incoming)
    expect(merged.transcriptStatus).toBe('available')
    expect(merged.transcript.segments[0]?.text).toBe('Contenu conservé')
    expect(merged.transcriptProjection).toMatchObject({
      revision: 4,
      refreshPending: true,
      pendingRevision: 5,
    })
  })

  it('does not forget a newer unreadable revision after a stale response', () => {
    const digest = 'a'.repeat(64)
    const current = {
      version: 1,
      state: 'available',
      revision: 4,
      digest,
      source: null,
    } as const
    const pending = mergeContentProjection(current, {
      version: 1,
      state: 'unknown',
      revision: 5,
      digest: null,
      source: null,
    })

    expect(mergeContentProjection(pending, current)).toEqual(pending)
  })

  it('keeps one immutable identity for a readable revision', () => {
    const current = {
      version: 1,
      state: 'available',
      revision: 4,
      digest: 'a'.repeat(64),
      source: null,
    } as const
    const conflicting = {
      ...current,
      digest: 'b'.repeat(64),
    }

    expect(mergeContentProjection(current, conflicting)).toMatchObject({
      digest: current.digest,
      refreshPending: true,
    })
  })

  it('does not restore a cached summary after the transcript advances', () => {
    const digestA = 'a'.repeat(64)
    const digestB = 'b'.repeat(64)
    const response = (
      summaryRevision: number,
      transcriptRevision: number,
      transcriptDigest: string
    ) =>
      normalizeHistoryDetail({
        id: 'meeting-1',
        started_at: '2026-09-29T00:00:00Z',
        summary_projection: {
          version: 1,
          state: 'available',
          revision: summaryRevision,
          digest: 'c'.repeat(64),
          source: { revision: 1, digest: digestA },
        },
        transcript_projection: {
          version: 1,
          state: 'available',
          revision: transcriptRevision,
          digest: transcriptDigest,
          source: null,
        },
        summary: { text: 'Summary for transcript A' },
        transcript: {
          segments: [{ id: 's1', start_ms: 0, text: 'Transcript' }],
        },
      })
    const current = response(8, 1, digestA)
    const incoming = response(7, 2, digestB)

    if (!current || !incoming) throw new Error('Expected valid meeting details')
    const merged = mergeHistoryDetail(current, incoming)
    expect(merged.summaryStatus).toBe('unknown')
    expect(merged.summaryProjection.revision).toBe(8)
    expect(merged.summary.paragraphs).toEqual([])
    expect(merged.transcriptProjection.digest).toBe(digestB)
  })
})

describe('normalizeHistoryDetail', () => {
  const base = {
    id: 'meeting-1',
    title: 'Point',
    started_at: '2026-09-20T10:00:00Z',
    summary_status: 'available',
    transcript_status: 'available',
  }

  it('shows available content as paragraphs, sections and ordered segments', () => {
    const detail = normalizeHistoryDetail({
      ...base,
      summary: {
        status: 'available',
        text: 'First paragraph.\n\nSecond paragraph.',
        sections: [{ title: 'Actions', items: ['Call back', ''] }],
      },
      transcript: {
        status: 'available',
        segments: [
          { id: 'b', start_ms: 5000, speaker: 'B', text: 'Later' },
          { id: 'a', start_ms: 1000, speaker: null, text: 'Earlier' },
          { id: 'empty', start_ms: 2000, text: '' },
        ],
      },
    })

    expect(detail?.summary.paragraphs).toEqual([
      'First paragraph.',
      'Second paragraph.',
    ])
    expect(detail?.summary.sections).toEqual([
      { title: 'Actions', items: ['Call back'] },
    ])
    expect(detail?.transcript.segments.map((segment) => segment.id)).toEqual([
      'a',
      'b',
    ])
  })

  it('treats legacy available content without readable data as completed', () => {
    const detail = normalizeHistoryDetail({
      ...base,
      summary: { status: 'available', text: '   ' },
      transcript: { status: 'available', segments: [] },
    })

    expect(detail?.summary.status).toBe('completed_empty')
    expect(detail?.transcript.status).toBe('completed_empty')
    expect(detail?.summaryStatus).toBe('completed_empty')
  })

  it('keeps processing and failed states without content', () => {
    const detail = normalizeHistoryDetail({
      ...base,
      summary: { status: 'processing', text: 'draft that must not leak' },
      transcript: { status: 'failed' },
    })

    expect(detail?.summary).toEqual({
      status: 'transcribing',
      projection: expect.objectContaining({ state: 'transcribing' }),
      paragraphs: [],
      sections: [],
    })
    expect(detail?.transcript.status).toBe('failed')
  })

  it('hides a summary whose source does not match the transcript', () => {
    const transcriptDigest = 'a'.repeat(64)
    const detail = normalizeHistoryDetail({
      ...base,
      summary_projection: {
        version: 1,
        state: 'available',
        revision: 7,
        digest: 'b'.repeat(64),
        source: { revision: 3, digest: 'c'.repeat(64) },
      },
      transcript_projection: {
        version: 1,
        state: 'available',
        revision: 4,
        digest: transcriptDigest,
        source: null,
      },
      summary: { text: 'Stale summary' },
      transcript: {
        segments: [{ id: 's1', start_ms: 0, text: 'Current transcript' }],
      },
    })

    expect(detail?.summaryStatus).toBe('unknown')
    expect(detail?.summary.paragraphs).toEqual([])
    expect(detail?.summaryProjection.refreshPending).toBe(true)
  })

  it('rejects a response without identifier or date', () => {
    expect(normalizeHistoryDetail({ title: 'x' })).toBeNull()
  })
})
