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
      loading: 'loading',
      home: 'home',
    })
  })
})
