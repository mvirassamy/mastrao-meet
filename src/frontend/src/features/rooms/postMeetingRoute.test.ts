import { describe, expect, it } from 'vitest'

import { buildPostMeetingPath, readPostMeetingRoute } from './postMeetingRoute'

describe('post-meeting route', () => {
  it('round-trips only bounded outcomes and valid room ids', () => {
    const path = buildPostMeetingPath({
      outcome: 'ended',
      roomId: 'room_0123456789abcdef0123456789abcdef',
    })

    expect(
      readPostMeetingRoute(new URL(path, 'https://meet.test').search)
    ).toEqual({
      outcome: 'ended',
      roomId: 'room_0123456789abcdef0123456789abcdef',
    })
  })

  it('drops arbitrary outcome and room values', () => {
    expect(
      readPostMeetingRoute('?outcome=https://evil.test&room_id=../secrets')
    ).toEqual({ outcome: undefined, roomId: undefined })
  })
})
