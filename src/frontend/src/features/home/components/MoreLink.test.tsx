import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MoreLink } from './MoreLink'

const config = vi.hoisted(() => ({
  data: undefined as { manifest_link?: string } | undefined,
}))

vi.mock('@/api/useConfig', () => ({ useConfig: () => config }))

describe('MoreLink', () => {
  afterEach(() => {
    cleanup()
    config.data = undefined
  })

  it('renders nothing without a configured manifest link', () => {
    config.data = {}
    const { container } = render(<MoreLink />)
    expect(container.innerHTML).toBe('')
  })

  it('opens the configured manifest link in a new tab', () => {
    config.data = { manifest_link: 'https://mastrao.com/visio' }
    render(<MoreLink />)
    const link = screen.getByRole('link')
    expect(link.getAttribute('href')).toBe('https://mastrao.com/visio')
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toBe('noopener noreferrer')
  })
})
