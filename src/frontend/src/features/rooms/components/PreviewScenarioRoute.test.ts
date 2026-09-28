import { describe, expect, it } from 'vitest'
import { getPreviewContent, previewScreens } from '@/dev/previewScenarioRoute'

describe('getPreviewContent', () => {
  it('maps every gallery screen to its intended preview content', () => {
    const actual = Object.fromEntries(
      previewScreens.map(([scenario]) => [
        scenario,
        getPreviewContent(scenario),
      ])
    )

    expect(actual).toEqual({
      components: 'gallery',
      accessibility: 'room',
      join: 'join',
      'devices-off': 'join',
      reconnecting: 'room',
      'recording-starting': 'room',
      'recording-active': 'room',
      'recording-stopping': 'room',
      room: 'room',
      notifications: 'room',
      consent: 'consent',
      invitation: 'invitation',
      feedback: 'feedback',
      error: 'error',
      'not-found': 'not-found',
      loading: 'loading',
      home: 'home',
      'authenticated-home': 'authenticated-home',
      'later-meeting-dialog': 'authenticated-home',
      history: 'meeting-history',
      'history-loading': 'meeting-history',
      'history-empty': 'meeting-history',
      'history-error': 'meeting-history',
      'history-detail': 'meeting-history',
      'history-detail-processing': 'meeting-history',
      'history-detail-absent': 'meeting-history',
      'history-detail-summary-request': 'meeting-history',
      'history-detail-error': 'meeting-history',
    })
  })
})
