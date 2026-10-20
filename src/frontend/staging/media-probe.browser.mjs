import assert from 'node:assert/strict'
import { test } from 'node:test'
import { chromium } from 'playwright'
import {
  installMediaProbe,
  readMediaSnapshot,
  hasBidirectionalMediaProgress,
} from './media-probe.mjs'

test(
  'real Chromium probe observes synthetic audio/video and rejects closed peers',
  { timeout: 35_000 },
  async () => {
    const browser = await chromium.launch({
      args: ['--autoplay-policy=no-user-gesture-required'],
    })
    try {
      const context = await browser.newContext()
      await installMediaProbe(context)
      await context.route('https://meet-probe.invalid/**', (route) =>
        route.fulfill({
          contentType: 'text/html',
          body: '<!doctype html><title>Synthetic media probe</title>',
        })
      )
      const page = await context.newPage()
      await page.goto('https://meet-probe.invalid/')
      await page.evaluate(async () => {
        const audio = new AudioContext()
        await audio.resume()
        const oscillator = audio.createOscillator()
        const destination = audio.createMediaStreamDestination()
        oscillator.connect(destination)
        oscillator.start()
        const canvas = document.createElement('canvas')
        canvas.width = 160
        canvas.height = 120
        const drawing = canvas.getContext('2d')
        let frame = 0
        const animation = setInterval(() => {
          drawing.fillStyle = `rgb(${frame++ % 255}, 64, 128)`
          drawing.fillRect(0, 0, 160, 120)
        }, 50)
        const video = canvas.captureStream(10)
        const first = new RTCPeerConnection({ iceServers: [] })
        const second = new RTCPeerConnection({ iceServers: [] })
        for (const peer of [first, second]) {
          peer.ontrack = ({ track }) => {
            const element = document.createElement(track.kind)
            element.autoplay = true
            element.srcObject = new MediaStream([track])
            document.body.append(element)
            void element.play()
          }
        }
        first.onicecandidate = ({ candidate }) => {
          if (candidate) void second.addIceCandidate(candidate)
        }
        second.onicecandidate = ({ candidate }) => {
          if (candidate) void first.addIceCandidate(candidate)
        }
        const tracks = [...destination.stream.getTracks(), ...video.getTracks()]
        for (const peer of [first, second]) {
          for (const track of tracks) peer.addTrack(track)
        }
        await first.setLocalDescription(await first.createOffer())
        await second.setRemoteDescription(first.localDescription)
        await second.setLocalDescription(await second.createAnswer())
        await first.setRemoteDescription(second.localDescription)
        window.closeSyntheticPeers = async () => {
          first.close()
          second.close()
          for (const track of tracks) track.stop()
          clearInterval(animation)
          oscillator.stop()
          await audio.close()
        }
      })
      let before = await readMediaSnapshot(page)
      let progressed = false
      for (let attempt = 0; attempt < 20; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 400))
        const after = await readMediaSnapshot(page)
        progressed = hasBidirectionalMediaProgress(before, after)
        before = after
        if (progressed) break
      }
      assert.equal(
        progressed,
        true,
        'Synthetic RTP media must pass the same probe used on staging'
      )
      await page.evaluate(() => window.closeSyntheticPeers())
      const closed = await readMediaSnapshot(page)
      assert.equal(hasBidirectionalMediaProgress(before, closed), false)
    } finally {
      await browser.close()
    }
  }
)
