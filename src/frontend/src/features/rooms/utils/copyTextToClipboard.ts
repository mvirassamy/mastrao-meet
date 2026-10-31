const LEGACY_COPY_ERROR = 'Unable to copy text to the clipboard'

const copyWithSelection = (text: string) => {
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.readOnly = true
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  textarea.style.pointerEvents = 'none'

  document.body.append(textarea)
  textarea.select()

  try {
    if (!document.execCommand('copy')) throw new Error(LEGACY_COPY_ERROR)
  } finally {
    textarea.remove()
  }
}

export const copyTextToClipboard = async (text: string) => {
  try {
    if (!navigator.clipboard?.writeText)
      throw new Error('Clipboard API unavailable')
    await navigator.clipboard.writeText(text)
  } catch {
    copyWithSelection(text)
  }
}
