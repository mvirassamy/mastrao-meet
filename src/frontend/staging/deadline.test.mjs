import assert from 'node:assert/strict'
import { test } from 'node:test'
import { within } from './deadline.mjs'

test('a resolved operation preserves its result and clears its deadline', async (t) => {
  const clear = t.mock.method(globalThis, 'clearTimeout')
  const result = { synthetic: true }
  assert.equal(await within(Promise.resolve(result), 1000), result)
  assert.equal(clear.mock.callCount(), 1)
})

test('a rejected operation preserves its error and clears its deadline', async (t) => {
  const clear = t.mock.method(globalThis, 'clearTimeout')
  const error = new Error('synthetic failure')
  await assert.rejects(
    within(Promise.reject(error), 1000),
    (actual) => actual === error
  )
  assert.equal(clear.mock.callCount(), 1)
})

test('a never-resolving operation rejects at the deadline and clears its timer', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const clear = t.mock.method(globalThis, 'clearTimeout')
  const pending = within(new Promise(() => {}), 10)
  const rejected = assert.rejects(pending, /deadline/i)
  t.mock.timers.tick(10)
  await rejected
  assert.equal(clear.mock.callCount(), 1)
})
