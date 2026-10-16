export const PERSONAL_NOTE_LIMIT = 100_000

/** Reject the whole edit so a length limit never removes existing text. */
export function applyNoteEdit(value: string, previousContent: string) {
  // The API counts Unicode code points, rather than UTF-16 code units.
  const length = Array.from(value).length
  if (length > PERSONAL_NOTE_LIMIT) {
    return {
      content: previousContent,
      length: Array.from(previousContent).length,
      wasRejected: true,
    }
  }
  return {
    content: value,
    length,
    wasRejected: false,
  }
}
