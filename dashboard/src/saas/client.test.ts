import { afterEach, describe, expect, it, vi } from 'vitest'
import { createSaasClient, getSession, setSession } from './client'

describe('saas session token', () => {
  afterEach(() => {
    setSession(undefined)
    vi.restoreAllMocks()
  })

  it('persists and clears the session token', () => {
    expect(getSession()).toBeUndefined()
    setSession('abc.def.ghi')
    expect(getSession()).toBe('abc.def.ghi')
    setSession(undefined)
    expect(getSession()).toBeUndefined()
  })

  it('sends the bearer token on requests when set', async () => {
    setSession('tok-123')
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ members: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await createSaasClient('http://api.test').listMembers()
    const [, init] = fetchMock.mock.calls[0]
    const headers = (init as RequestInit).headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer tok-123')
  })

  it('syncs the inbox and posts to the sync endpoint', async () => {
    setSession('tok')
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ results: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await createSaasClient('http://api.test').syncInbox()
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/inbox/sync')
    expect((init as RequestInit).method).toBe('POST')
  })

  it('approves an action by id', async () => {
    setSession('tok')
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ action: { id: 'a1', status: 'executed' } }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const res = await createSaasClient('http://api.test').approveAction('a1')
    expect(res.action.status).toBe('executed')
    expect(String(fetchMock.mock.calls[0][0])).toContain('/inbox/actions/a1/approve')
  })

  it('requests a password reset', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ status: 'ok' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await createSaasClient('http://api.test').forgotPassword('a@b.com')
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/auth/forgot-password')
    expect((init as RequestInit).method).toBe('POST')
  })

  it('surfaces the API detail message on error', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Invalid email or password.' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await expect(createSaasClient('http://api.test').login('a@b.com', 'x')).rejects.toThrow(
      'Invalid email or password.',
    )
  })
})
