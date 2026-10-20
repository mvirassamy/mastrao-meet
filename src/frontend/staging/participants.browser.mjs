import assert from 'node:assert/strict'
import { test } from 'node:test'
import { chromium } from 'playwright'
import { waitForExpectedParticipants } from './participants.mjs'

const expected = ['synthetic-a', 'synthetic-b']

async function rosterPage(browser, ids) {
  const page = await browser.newPage()
  await page.setContent(
    '<button aria-controls="contributors">Contributors</button>' +
      '<div id="contributors"></div>'
  )
  await page.evaluate((ids) => {
    for (const id of ids) {
      const row = document.createElement('div')
      row.role = 'listitem'
      row.id = id
      document.getElementById('contributors').append(row)
    }
  }, ids)
  return page
}

test(
  'Chromium waits for the second exact identity and bounds invalid rosters',
  { timeout: 25_000 },
  async () => {
    const browser = await chromium.launch()
    try {
      const page = await rosterPage(browser, [expected[0]])
      await page.evaluate((id) => {
        setTimeout(() => {
          const row = document.createElement('div')
          row.role = 'listitem'
          row.id = id
          document.getElementById('contributors').append(row)
        }, 300)
      }, expected[1])
      const observation = {}
      await waitForExpectedParticipants(page, expected, observation)
      assert.deepEqual(observation.roster, {
        listFound: true,
        count: 2,
        expectedSetMatches: true,
      })

      await Promise.all(
        [
          [expected[0]],
          [expected[0], 'synthetic-stranger'],
          [...expected, 'synthetic-stranger'],
        ].map(async (ids) => {
          const invalid = await rosterPage(browser, ids)
          const failed = {}
          await assert.rejects(
            waitForExpectedParticipants(invalid, expected, failed)
          )
          assert.deepEqual(failed.roster, {
            listFound: true,
            count: ids.length,
            expectedSetMatches: false,
          })
        })
      )
    } finally {
      await browser.close()
    }
  }
)
