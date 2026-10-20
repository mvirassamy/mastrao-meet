import { readFile, realpath, stat } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'

export const STAGING_ORIGIN = 'https://meet.mastrao-staging.com'

export function validateFixture(value) {
  if (value?.schemaVersion !== 1 || value.approvedForSyntheticMedia !== true) {
    throw new Error('A dedicated synthetic meeting fixture must be approved')
  }
  const room = new URL(value.roomUrl)
  if (
    room.origin !== STAGING_ORIGIN ||
    room.username ||
    room.password ||
    room.search ||
    room.hash ||
    !/^\/room_[a-f0-9]{32}$/.test(room.pathname)
  ) {
    throw new Error('An existing canonical staging meeting is required')
  }
  if (!Array.isArray(value.participants) || value.participants.length !== 2) {
    throw new Error('Two existing participant sessions are required')
  }
  for (const participant of value.participants) {
    if (
      typeof participant?.expectedUserId !== 'string' ||
      participant.expectedUserId.length === 0 ||
      typeof participant.expectedRtcIdentity !== 'string' ||
      participant.expectedRtcIdentity.length === 0 ||
      typeof participant.storageStatePath !== 'string' ||
      participant.storageStatePath.length === 0
    ) {
      throw new Error(
        'Participant identity and private session path are required'
      )
    }
  }
  if (
    value.participants[0].expectedUserId ===
      value.participants[1].expectedUserId ||
    value.participants[0].expectedRtcIdentity ===
      value.participants[1].expectedRtcIdentity
  ) {
    throw new Error('The participants must be different users')
  }
  return value
}

async function readPrivateJson(path) {
  const metadata = await stat(path)
  if (!metadata.isFile() || (metadata.mode & 0o077) !== 0) {
    throw new Error('Fixture and session files must be private regular files')
  }
  return JSON.parse(await readFile(path, 'utf8'))
}

export async function loadFixture(path) {
  const fixturePath = await realpath(path)
  const fixture = validateFixture(await readPrivateJson(fixturePath))
  const participants = []
  const sessionPaths = new Set()
  for (const participant of fixture.participants) {
    const sessionPath = await realpath(
      resolve(dirname(fixturePath), participant.storageStatePath)
    )
    if (sessionPaths.has(sessionPath))
      throw new Error('Two separate sessions are required')
    sessionPaths.add(sessionPath)
    const storageState = await readPrivateJson(sessionPath)
    if (
      !Array.isArray(storageState.cookies) ||
      !Array.isArray(storageState.origins)
    ) {
      throw new Error('A Playwright storage state is required')
    }
    participants.push({
      expectedUserId: participant.expectedUserId,
      expectedRtcIdentity: participant.expectedRtcIdentity,
      storageState,
    })
  }
  return { roomUrl: fixture.roomUrl, participants }
}
