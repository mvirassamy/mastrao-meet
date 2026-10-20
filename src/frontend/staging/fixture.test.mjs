import assert from 'node:assert/strict'
import { mkdtemp, writeFile, chmod, symlink } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { spawnSync } from 'node:child_process'
import { validateFixture, loadFixture, STAGING_ORIGIN } from './fixture.mjs'

const fixture = () => ({
  schemaVersion: 1,
  approvedForSyntheticMedia: true,
  roomUrl: `${STAGING_ORIGIN}/room_${'a'.repeat(32)}`,
  participants: [
    {
      expectedUserId: 'synthetic-user-a',
      expectedRtcIdentity: 'rtc-a',
      storageStatePath: 'a.json',
    },
    {
      expectedUserId: 'synthetic-user-b',
      expectedRtcIdentity: 'rtc-b',
      storageStatePath: 'b.json',
    },
  ],
})

test('requires an approved canonical staging room and two distinct users', () => {
  assert.equal(validateFixture(fixture()).participants.length, 2)
  for (const change of [
    (value) => {
      value.approvedForSyntheticMedia = false
    },
    (value) => {
      value.roomUrl = 'https://meet.mastrao.com/room_' + 'a'.repeat(32)
    },
    (value) => {
      value.roomUrl += '?token=private-marker'
    },
    (value) => {
      value.roomUrl = `${STAGING_ORIGIN}/abc-abcd-abc`
    },
    (value) => {
      value.roomUrl = `${STAGING_ORIGIN}/room_${'a'.repeat(32)}#private-marker`
    },
    (value) => {
      value.participants[1].expectedUserId =
        value.participants[0].expectedUserId
    },
    (value) => {
      value.participants[1].expectedRtcIdentity =
        value.participants[0].expectedRtcIdentity
    },
    (value) => {
      delete value.participants[0].expectedRtcIdentity
    },
    (value) => {
      value.participants.pop()
    },
    (value) => {
      value.participants[1].storageStatePath = ''
    },
  ]) {
    const value = fixture()
    change(value)
    assert.throws(() => validateFixture(value))
  }
})

test('private fixture resolves session paths and rejects world-readable or reused sessions', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'meet-synthetic-fixture-'))
  const fixturePath = join(directory, 'fixture.json')
  const state = { cookies: [], origins: [] }
  for (const name of ['a.json', 'b.json']) {
    await writeFile(join(directory, name), JSON.stringify(state), {
      mode: 0o600,
    })
  }
  await writeFile(fixturePath, JSON.stringify(fixture()), { mode: 0o600 })
  assert.equal((await loadFixture(fixturePath)).participants.length, 2)
  await chmod(join(directory, 'b.json'), 0o644)
  await assert.rejects(loadFixture(fixturePath), /private regular files/)
  await chmod(join(directory, 'b.json'), 0o600)
  const reused = fixture()
  await symlink(join(directory, 'a.json'), join(directory, 'alias.json'))
  reused.participants[1].storageStatePath = 'alias.json'
  await writeFile(fixturePath, JSON.stringify(reused))
  await assert.rejects(loadFixture(fixturePath), /separate sessions/)
})

test('CLI failure never prints paths, credentials or browser exceptions', () => {
  const result = spawnSync(process.execPath, ['staging/run.mjs'], {
    cwd: new URL('..', import.meta.url),
    env: {
      ...process.env,
      MEET_STAGING_FIXTURE: '/missing/private-marker.json',
    },
    encoding: 'utf8',
  })
  assert.equal(result.status, 1)
  assert.equal(result.stderr, '')
  assert.equal(result.stdout.includes('private-marker'), false)
  assert.deepEqual(JSON.parse(result.stdout), {
    schemaVersion: 1,
    status: 'MEET_TWO_PARTY_BROWSER_NOT_CONFIRMED',
    stage: 'FIXTURE',
    releaseCorrelationProven: false,
    roomCreationProven: false,
    networkOutageRecoveryProven: false,
    recordingAndTranscriptionProven: false,
    automaticMeetDeliveryProven: false,
  })
})
