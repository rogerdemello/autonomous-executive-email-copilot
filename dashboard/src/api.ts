// Shared API client: timeouts, retry/backoff, and typed errors so components
// don't hand-roll fetch + error handling.

export class ApiError extends Error {
  status?: number
  constructor(message: string, status?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export interface RequestOptions {
  timeoutMs?: number
  retries?: number
}

const DEFAULT_TIMEOUT_MS = 10000

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function request<T>(url: string, init: RequestInit, opts: RequestOptions = {}): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, retries = 0 } = opts
  let lastError: unknown

  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    try {
      const res = await fetch(url, { ...init, signal: controller.signal })
      clearTimeout(timer)
      if (!res.ok) {
        let detail = `HTTP ${res.status}`
        try {
          const body = await res.json()
          if (body && typeof body.detail === 'string') detail = body.detail
        } catch {
          // non-JSON error body; keep the status-based message
        }
        throw new ApiError(detail, res.status)
      }
      if (res.status === 204) return undefined as T
      return (await res.json()) as T
    } catch (err) {
      clearTimeout(timer)
      lastError = err
      // Never retry client errors (4xx) — they won't succeed on retry.
      if (err instanceof ApiError && err.status && err.status < 500) throw err
      if (attempt < retries) await delay(200 * 2 ** attempt)
    }
  }
  throw lastError instanceof Error ? lastError : new ApiError('Request failed')
}

export interface ApiClient {
  get<T>(path: string, opts?: RequestOptions): Promise<T>
  post<T>(path: string, body?: unknown, opts?: RequestOptions): Promise<T>
  put<T>(path: string, body?: unknown, opts?: RequestOptions): Promise<T>
}

// App-global bearer token, read at request time so existing (memoized) clients
// pick up changes without rebuilding. Set via setAuthToken().
let authToken: string | undefined

/** Set (or clear, with an empty value) the bearer token sent on requests. */
export function setAuthToken(token: string | undefined): void {
  authToken = token && token.trim() ? token.trim() : undefined
}

function buildHeaders(json: boolean): HeadersInit | undefined {
  const headers: Record<string, string> = {}
  if (json) headers['Content-Type'] = 'application/json'
  // Sent only when a token is configured; mutating routes require it when the
  // API has API_AUTH_TOKEN set. Safe routes ignore it.
  if (authToken) headers.Authorization = `Bearer ${authToken}`
  return Object.keys(headers).length ? headers : undefined
}

function bodyInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: buildHeaders(body !== undefined),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  }
}

/**
 * Default API base when VITE_API_BASE is not set.
 * - Vite dev server (`npm run dev`): the API runs separately on :8000.
 * - Production build (served by the app itself in the Docker image, locally or
 *   on Render/Cloud Run): the API is same-origin, so use the page origin.
 */
export function defaultApiBase(): string {
  if (import.meta.env.DEV) return 'http://127.0.0.1:8000'
  if (typeof window !== 'undefined') return window.location.origin
  return 'http://127.0.0.1:8000'
}

export function createApiClient(baseUrl: string): ApiClient {
  const base = baseUrl.replace(/\/+$/, '')
  return {
    get: <T>(path: string, opts?: RequestOptions) =>
      request<T>(
        `${base}${path}`,
        { method: 'GET', headers: buildHeaders(false) },
        {
          retries: 2,
          ...opts,
        },
      ),
    post: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
      request<T>(`${base}${path}`, bodyInit('POST', body), opts),
    put: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
      request<T>(`${base}${path}`, bodyInit('PUT', body), opts),
  }
}
