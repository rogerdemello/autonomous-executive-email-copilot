import { describe, it, expect, vi, afterEach } from 'vitest'
import { createApiClient, ApiError } from './api'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('createApiClient', () => {
  it('returns parsed JSON on success', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })
    vi.stubGlobal('fetch', fetchMock)

    const data = await createApiClient('http://x').get<{ status: string }>('/health')

    expect(data.status).toBe('ok')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('builds the URL and strips a trailing slash from the base', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)

    await createApiClient('http://x/').get('/health')

    expect(fetchMock).toHaveBeenCalledWith(
      'http://x/health',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('throws ApiError with the server detail and does not retry on 4xx', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: false, status: 400, json: async () => ({ detail: 'bad input' }) })
    vi.stubGlobal('fetch', fetchMock)

    await expect(createApiClient('http://x').get('/x')).rejects.toMatchObject({
      name: 'ApiError',
      status: 400,
      message: 'bad input',
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('retries on 5xx and then throws', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)

    await expect(createApiClient('http://x').get('/x', { retries: 1 })).rejects.toBeInstanceOf(
      ApiError,
    )
    expect(fetchMock).toHaveBeenCalledTimes(2) // initial + 1 retry
  })
})
