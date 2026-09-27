import { describe, expect, it } from 'vitest'
import {
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
      summaryStatus: 'absent',
      transcriptStatus: 'failed',
    })
    expect(page.nextCursor).toBe('next')
  })
})

describe('normalizeContentStatus', () => {
  it('never turns an unknown status into available content', () => {
    expect(normalizeContentStatus('available')).toBe('available')
    expect(normalizeContentStatus(undefined)).toBe('absent')
    expect(normalizeContentStatus('ready')).toBe('absent')
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

  it('treats "available" without readable content as absent', () => {
    const detail = normalizeHistoryDetail({
      ...base,
      summary: { status: 'available', text: '   ' },
      transcript: { status: 'available', segments: [] },
    })

    expect(detail?.summary.status).toBe('absent')
    expect(detail?.transcript.status).toBe('absent')
    expect(detail?.summaryStatus).toBe('absent')
  })

  it('keeps processing and failed states without content', () => {
    const detail = normalizeHistoryDetail({
      ...base,
      summary: { status: 'processing', text: 'draft that must not leak' },
      transcript: { status: 'failed' },
    })

    expect(detail?.summary).toEqual({
      status: 'processing',
      paragraphs: [],
      sections: [],
    })
    expect(detail?.transcript.status).toBe('failed')
  })

  it('rejects a response without identifier or date', () => {
    expect(normalizeHistoryDetail({ title: 'x' })).toBeNull()
  })
})
