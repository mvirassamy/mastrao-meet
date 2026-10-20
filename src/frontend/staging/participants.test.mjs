import assert from 'node:assert/strict'
import test from 'node:test'
import { validateParticipants } from './participants.mjs'

const expected = Object.freeze(['rtc-user-a', 'rtc-user-b'])

test('accepts exactly the two expected RTC identities in either UI order', () => {
  assert.equal(
    validateParticipants(['rtc-user-a', 'rtc-user-b'], expected),
    true
  )
  assert.equal(
    validateParticipants(['rtc-user-b', 'rtc-user-a'], expected),
    true
  )
})

test('a third participant cannot replace either expected user', () => {
  for (const actual of [
    ['rtc-user-a', 'rtc-third'],
    ['rtc-third', 'rtc-user-b'],
    ['rtc-third', 'rtc-fourth'],
  ]) {
    assert.equal(validateParticipants(actual, expected), false)
  }
})

test('an additional participant or visible bot blocks an otherwise matching roster', () => {
  for (const actual of [
    ['rtc-bot', ...expected],
    [expected[0], 'rtc-third', expected[1]],
    [...expected, 'rtc-third'],
    [...expected, 'rtc-third', 'rtc-fourth'],
  ]) {
    assert.equal(validateParticipants(actual, expected), false)
  }
})

test('duplicate actual or expected identities cannot stand in for two accounts', () => {
  for (const duplicate of [
    ['rtc-user-a', 'rtc-user-a'],
    ['rtc-user-b', 'rtc-user-b'],
  ]) {
    assert.equal(validateParticipants(duplicate, expected), false)
    assert.equal(validateParticipants(expected, duplicate), false)
    assert.equal(validateParticipants(duplicate, duplicate), false)
  }
  assert.equal(
    validateParticipants([...expected, expected[0]], expected),
    false
  )
})

test('compares both expected accounts rather than only the count or one identity', () => {
  assert.equal(
    validateParticipants(expected, ['rtc-user-a', 'rtc-third']),
    false
  )
  assert.equal(
    validateParticipants(expected, ['rtc-third', 'rtc-user-b']),
    false
  )
  assert.equal(
    validateParticipants(expected, ['rtc-third', 'rtc-fourth']),
    false
  )
})

test('missing lists, missing users and non-array inputs fail closed on either side', () => {
  const malformed = [
    undefined,
    null,
    false,
    2,
    'rtc-user-a,rtc-user-b',
    {},
    { 0: expected[0], 1: expected[1], length: 2 },
    new Set(expected),
    [],
    [expected[0]],
    Array(2),
  ]
  for (const value of malformed) {
    assert.equal(validateParticipants(value, expected), false)
    assert.equal(validateParticipants(expected, value), false)
  }
  assert.equal(validateParticipants(undefined, undefined), false)
})

test('missing or malformed row identities fail closed without string coercion', () => {
  for (const value of [
    undefined,
    null,
    '',
    false,
    0,
    NaN,
    {},
    [],
    new String(expected[0]),
  ]) {
    for (const index of [0, 1]) {
      const actual = [...expected]
      actual[index] = value
      assert.equal(validateParticipants(actual, expected), false)
      const wanted = [...expected]
      wanted[index] = value
      assert.equal(validateParticipants(expected, wanted), false)
      assert.equal(validateParticipants(wanted, wanted), false)
    }
  }
})

test('uses exact identities rather than display names, case folding or trimming', () => {
  for (const actual of [
    ['RTC-USER-A', expected[1]],
    [` ${expected[0]}`, expected[1]],
    [expected[0], `${expected[1]} `],
    ['Display name A', 'Display name B'],
  ]) {
    assert.equal(validateParticipants(actual, expected), false)
  }
  const opaqueIds = ['rtc:user/a-123', 'rtc:user/b-456']
  assert.equal(validateParticipants([...opaqueIds].reverse(), opaqueIds), true)
})

test('never changes or sorts either caller-owned array', () => {
  const actual = Object.freeze(['rtc-user-b', 'rtc-user-a'])
  const before = [...actual]
  assert.equal(validateParticipants(actual, expected), true)
  assert.deepEqual(actual, before)
  assert.deepEqual(expected, ['rtc-user-a', 'rtc-user-b'])
})
