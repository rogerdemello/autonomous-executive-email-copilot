import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import App from './App'

// A minimal WebSocket stub so useDashboardSocket does not touch the network.
class FakeWebSocket {
  static OPEN = 1
  readyState = 0
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  send() {}
  close() {}
}

beforeEach(() => {
  vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket)
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok' }),
    }),
  )
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('App accessibility landmarks', () => {
  it('exposes banner, navigation, and main landmarks', () => {
    render(<App />)
    expect(screen.getByRole('banner')).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: /primary/i })).toBeInTheDocument()
    expect(screen.getByRole('main')).toBeInTheDocument()
  })

  it('renders the navigation as an accessible tablist', () => {
    render(<App />)
    const tablist = screen.getByRole('tablist')
    const tabs = within(tablist).getAllByRole('tab')
    expect(tabs.length).toBeGreaterThanOrEqual(6)
    // The default Inbox tab is selected and its panel is present.
    const inboxTab = screen.getByRole('tab', { name: 'Inbox' })
    expect(inboxTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toBeInTheDocument()
  })

  it('exposes the live connection status as a live region', () => {
    render(<App />)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })
})
