import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import Inbox from './Inbox'

afterEach(() => {
  vi.restoreAllMocks()
})

const observation = {
  emails: [
    {
      id: 'e1',
      sender: 'client@acme.com',
      sender_role: 'client',
      subject: 'Contract review',
      body: 'Please review.',
      priority_hint: 'high',
      deadline_minutes: 60,
      business_value: 0.8,
      risk_tag: 'none',
    },
  ],
  time_remaining: 60,
  pending_actions: [],
  risk_level: 'medium',
  current_minute: 0,
  persona: 'balanced',
  remaining_interruptions: 0,
}

describe('Inbox', () => {
  it('renders emails returned by the API', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => observation }),
    )

    render(<Inbox apiBase="http://x" />)

    await waitFor(() => expect(screen.getByText('Contract review')).toBeInTheDocument())
    expect(screen.getByText(/client@acme.com/)).toBeInTheDocument()
  })

  it('shows an error message when the API fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({ detail: 'boom' }) }),
    )

    render(<Inbox apiBase="http://x" />)

    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument())
  })
})
