import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

beforeEach(() => {
  window.sessionStorage.clear()
  vi.resetModules()
})
afterEach(() => vi.restoreAllMocks())

const speaker = { label: 'Alice Martin', color: 'hsl(120, 50%, 40%)' }
const storageKey = 'mastrao-live-transcript-speakers-v1:room'

describe('live transcript speaker cache', () => {
  it('restores session storage after the in-memory module is reloaded', async () => {
    const cache = await import('./liveTranscriptSpeakerCache')
    cache.writeKnownSpeakers('room', new Map([['alice', speaker]]))
    vi.resetModules()
    const restored = await import('./liveTranscriptSpeakerCache')
    expect(restored.readKnownSpeakers('room').get('alice')).toMatchObject(
      speaker
    )
  })

  it('does not read storage again on subsequent room reads', async () => {
    const cache = await import('./liveTranscriptSpeakerCache')
    const storage = vi.spyOn(window, 'sessionStorage', 'get')
    cache.readKnownSpeakers('room')
    const reads = storage.mock.calls.length
    cache.readKnownSpeakers('room')
    expect(storage).toHaveBeenCalledTimes(reads)
  })

  it('retains memory when session storage writes fail', async () => {
    const cache = await import('./liveTranscriptSpeakerCache')
    vi.spyOn(
      Object.getPrototypeOf(window.sessionStorage),
      'setItem'
    ).mockImplementation(() => {
      throw new Error('quota')
    })
    cache.writeKnownSpeakers('room', new Map([['alice', speaker]]))
    expect(cache.readKnownSpeakers('room').get('alice')).toEqual(speaker)
    expect(window.sessionStorage.getItem(storageKey)).toBeNull()
  })

  it('ignores malformed storage and invalid entries', async () => {
    const cache = await import('./liveTranscriptSpeakerCache')
    window.sessionStorage.setItem(storageKey, '{broken')
    expect(cache.readKnownSpeakers('room').size).toBe(0)
    window.sessionStorage.setItem(
      `${storageKey}-2`,
      JSON.stringify([null, ['bad', {}], ['alice', speaker]])
    )
    expect([...cache.readKnownSpeakers('room-2').keys()]).toEqual(['alice'])
  })

  it('bounds persisted speakers and in-memory rooms', async () => {
    const cache = await import('./liveTranscriptSpeakerCache')
    cache.writeKnownSpeakers(
      'room',
      new Map(Array.from({ length: 501 }, (_, i) => [`speaker-${i}`, speaker]))
    )
    expect(cache.readKnownSpeakers('room').size).toBe(500)
    expect(cache.readKnownSpeakers('room').has('speaker-0')).toBe(false)
    expect(JSON.parse(window.sessionStorage.getItem(storageKey)!)).toHaveLength(
      500
    )
    vi.spyOn(window, 'sessionStorage', 'get').mockImplementation(() => {
      throw new Error('blocked')
    })
    for (let index = 0; index < 20; index++)
      cache.writeKnownSpeakers(`other-${index}`, new Map([['alice', speaker]]))
    expect(cache.readKnownSpeakers('room').size).toBe(0)
  })
})
