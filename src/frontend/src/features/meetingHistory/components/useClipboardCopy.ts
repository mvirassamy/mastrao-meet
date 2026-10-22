import { useEffect, useState } from 'react'

export type CopyState = 'idle' | 'copied' | 'failed'

const COPY_FEEDBACK_MS = 2000

/** Copies a text to the clipboard and keeps the outcome shown briefly. */
export function useClipboardCopy() {
  const [copy, setCopy] = useState<CopyState>('idle')

  useEffect(() => {
    if (copy === 'idle') return
    const timeout = window.setTimeout(() => setCopy('idle'), COPY_FEEDBACK_MS)
    return () => window.clearTimeout(timeout)
  }, [copy])

  async function copyText(text: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopy('copied')
    } catch {
      setCopy('failed')
    }
  }

  return { copy, copyText }
}
