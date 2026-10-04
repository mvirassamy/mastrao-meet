/** Number of participants: the server count, or the names it sent if more. */
export const participantTotal = (count: number | null, names: string[]) =>
  Math.max(count ?? 0, names.length)
