import type { MeetingHistoryItem, MeetingHistoryPage } from './types'

/** Loaded history meetings without duplicates, most recent first. */
export const historyItemsOf = (pages: MeetingHistoryPage[] = []) => {
  const seen = new Set<string>()
  return pages
    .flatMap((page) => page.items)
    .filter((item) => !seen.has(item.id) && Boolean(seen.add(item.id)))
    .sort(
      (a: MeetingHistoryItem, b: MeetingHistoryItem) =>
        b.startedAt.getTime() - a.startedAt.getTime()
    )
}
