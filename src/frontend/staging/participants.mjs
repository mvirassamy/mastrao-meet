import { within } from './deadline.mjs'

export function validateParticipants(actualIds, expectedIds) {
  if (!Array.isArray(actualIds) || !Array.isArray(expectedIds)) return false
  if (actualIds.length !== 2 || expectedIds.length !== 2) return false
  if (
    [...actualIds, ...expectedIds].some((id) => typeof id !== 'string' || !id)
  )
    return false
  if (new Set(actualIds).size !== 2 || new Set(expectedIds).size !== 2)
    return false
  return expectedIds.every((id) => actualIds.includes(id))
}

export async function openParticipants(page) {
  await page.getByRole('button', { name: 'See everyone', exact: true }).click()
  await page
    .getByRole('button', { name: /^Contributors/ })
    .waitFor({ state: 'visible' })
}

export async function requireExpectedParticipants(
  page,
  expectedIds,
  observation
) {
  const listId = await page
    .getByRole('button', { name: /^Contributors/ })
    .getAttribute('aria-controls')
  const actual = await within(
    page.evaluate((id) => {
      const list = document.getElementById(id)
      if (!list) return null
      return [...list.querySelectorAll('[role="listitem"]')].map(
        (row) => row.id
      )
    }, listId),
    5000
  )
  const matches = validateParticipants(actual, expectedIds)
  if (observation) {
    observation.roster = {
      listFound: Array.isArray(actual),
      count: Array.isArray(actual) ? actual.length : null,
      expectedSetMatches: matches,
    }
  }
  if (!matches) {
    throw new Error(
      'The meeting must contain exactly the two expected participants'
    )
  }
}
