import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyTextToClipboard } from './copyTextToClipboard'

const installClipboard = (writeText: ReturnType<typeof vi.fn>) => {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  })
}

const installLegacyCopy = (result: boolean) => {
  const execCommand = vi.fn().mockReturnValue(result)
  Object.defineProperty(document, 'execCommand', {
    configurable: true,
    value: execCommand,
  })
  return execCommand
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('copyTextToClipboard', () => {
  it('uses the asynchronous Clipboard API when it succeeds', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    const execCommand = installLegacyCopy(true)
    installClipboard(writeText)

    await copyTextToClipboard('invitation')

    expect(writeText).toHaveBeenCalledWith('invitation')
    expect(execCommand).not.toHaveBeenCalled()
  })

  it('falls back to a temporary selection when Clipboard API access is denied', async () => {
    const writeText = vi.fn().mockRejectedValue(new DOMException('Denied'))
    const execCommand = installLegacyCopy(true)
    installClipboard(writeText)

    await copyTextToClipboard('guest invitation')

    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(document.querySelector('textarea')).toBeNull()
  })

  it('fails when neither clipboard mechanism can copy the text', async () => {
    const writeText = vi.fn().mockRejectedValue(new DOMException('Denied'))
    installLegacyCopy(false)
    installClipboard(writeText)

    await expect(copyTextToClipboard('guest invitation')).rejects.toThrow(
      'Unable to copy text to the clipboard'
    )
    expect(document.querySelector('textarea')).toBeNull()
  })
})
