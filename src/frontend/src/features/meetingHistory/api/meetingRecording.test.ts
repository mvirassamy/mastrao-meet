import { describe, expect, it } from 'vitest'
import {
  mergeHistoryDetail,
  normalizeHistoryDetail,
  normalizeHistoryPage,
} from './meetingHistoryApi'
import {
  normalizeMeetingRecording,
  normalizeRecordingStatus,
} from './meetingRecording'
import { recordingAccessAction } from '../utils/recordingAccess'

const meeting = {
  id: 'meeting-1',
  started_at: '2026-10-11T10:00:00Z',
  summary: { status: 'available', text: 'Summary remains readable' },
  transcript: {
    status: 'available',
    segments: [{ id: 's1', start_ms: 0, text: 'Transcript remains readable' }],
  },
}
const access = {
  action_path: '/api/orgs/cabinet-test/meetings/recording/access',
  matter_ref: 'matter_0123456789',
  meeting_ref: 'meeting_01234567',
  recording_ref: 'recording_012345',
  artifact_ref: 'artifact_01234567',
}
const available = {
  status: 'available',
  retention_expires_at: 4_097_210_400,
  access,
}
const origin = 'https://platform.mastrao.test'

describe('additive history recording contract', () => {
  it('treats missing video fields in older payloads as absent', () => {
    const item = normalizeHistoryPage({ results: [meeting] }).items[0]
    expect(item.recordingStatus).toBe('absent')
    expect(item.recordingProjected).toBe(false)
    const detail = normalizeHistoryDetail(meeting)
    expect(detail?.recordingProjected).toBe(false)
    expect(detail?.recording).toEqual({
      status: 'absent',
      retentionExpiresAt: null,
      access: null,
    })
  })

  it.each([
    'absent',
    'processing',
    'available',
    'expired',
    'failed',
    'unknown',
  ])('reads the explicit list and detail status %s', (status) => {
    expect(normalizeRecordingStatus(status)).toBe(status)
    expect(
      normalizeHistoryPage({
        results: [{ ...meeting, recording_status: status }],
      }).items[0].recordingStatus
    ).toBe(status)
    expect(
      normalizeHistoryDetail({
        ...meeting,
        recording: { ...available, status },
      })?.recording.status
    ).toBe(status)
  })

  it.each([null, [], 42, 'available', {}, { status: 'ready' }])(
    'isolates malformed video metadata %j from readable text',
    (recording) => {
      const detail = normalizeHistoryDetail({ ...meeting, recording })
      expect(detail?.recording.status).toBe('unknown')
      expect(detail?.recording.access).toBeNull()
      expect(detail?.summary.paragraphs).toEqual(['Summary remains readable'])
      expect(detail?.transcript.segments[0].text).toBe(
        'Transcript remains readable'
      )
    }
  )

  it('keeps all exact references and builds only the Platform action', () => {
    const recording = normalizeMeetingRecording(available)
    expect(recording.access).toEqual({
      actionPath: access.action_path,
      matterRef: access.matter_ref,
      meetingRef: access.meeting_ref,
      recordingRef: access.recording_ref,
      artifactRef: access.artifact_ref,
    })
    expect(recordingAccessAction(recording, origin)).toBe(
      origin + access.action_path
    )
  })

  it.each([
    'matter_ref',
    'meeting_ref',
    'recording_ref',
    'artifact_ref',
    'action_path',
  ])('disables access when %s is missing or malformed', (field) => {
    for (const value of [
      undefined,
      null,
      '',
      ' ',
      'short',
      'a'.repeat(161),
      42,
    ]) {
      expect(
        normalizeMeetingRecording({
          ...available,
          access: { ...access, [field]: value },
        }).access
      ).toBeNull()
    }
  })

  it.each([
    undefined,
    null,
    'tomorrow',
    '2026-11-01',
    '2099-11-01T10:00:00Z',
    0,
    -1,
    4_097_210_400.5,
    Number.NaN,
    Number.POSITIVE_INFINITY,
    Number.MAX_SAFE_INTEGER,
  ])(
    'disables access without a valid retention timestamp %j',
    (retention_expires_at) => {
      expect(
        normalizeMeetingRecording({ ...available, retention_expires_at }).access
      ).toBeNull()
    }
  )

  it.each([
    'https://other.test/api/orgs/org/meetings/recording/access',
    '//other.test/api/orgs/org/meetings/recording/access',
    '/api/orgs/org/meetings/recording/access/',
    '/api/orgs/org/meetings/recording/access?redirect=elsewhere',
    '/api/orgs/org/meetings/recording/access#fragment',
    '/api/orgs/Cabinet/meetings/recording/access',
    '/api/orgs/cabinet_test/meetings/recording/access',
    '/api/orgs/../meetings/recording/access',
    '/api/orgs/%2e%2e/meetings/recording/access',
    '/api/orgs/org/meetings/recording/download',
    '/api/orgs/org/meetings/recording/access\n',
  ])('rejects unexpected action paths %s', (action_path) => {
    expect(
      normalizeMeetingRecording({
        ...available,
        access: { ...access, action_path },
      }).access
    ).toBeNull()
  })

  it.each([
    undefined,
    '',
    'garbage',
    'javascript:alert(1)',
    'http://platform.test',
    'https://user:secret@platform.test',
    'https://platform.test/path',
    'https://platform.test?redirect=elsewhere',
    'https://platform.test#fragment',
  ])('rejects absent or invalid Platform origins %j', (platformOrigin) => {
    expect(
      recordingAccessAction(
        normalizeMeetingRecording(available),
        platformOrigin
      )
    ).toBeNull()
  })

  it('allows configured local HTTP development origins', () => {
    expect(
      recordingAccessAction(
        normalizeMeetingRecording(available),
        'http://localhost:3911'
      )
    ).toBe('http://localhost:3911' + access.action_path)
  })

  it('revokes cached access on expiry, failure or a partial projection', () => {
    const current = normalizeHistoryDetail({
      ...meeting,
      recording: available,
    })!
    for (const recording of [
      { ...available, status: 'expired' },
      { ...available, status: 'failed' },
      { status: 'available' },
    ]) {
      const next = normalizeHistoryDetail({ ...meeting, recording })!
      const merged = mergeHistoryDetail(current, next)
      expect(merged.recording.access).toBeNull()
      expect(merged.summary.paragraphs).toEqual(current.summary.paragraphs)
    }
  })

  it('never opens a recording past its retention deadline', () => {
    expect(
      recordingAccessAction(
        normalizeMeetingRecording({
          ...available,
          retention_expires_at: 946_684_800,
        }),
        origin
      )
    ).toBeNull()
  })
})
