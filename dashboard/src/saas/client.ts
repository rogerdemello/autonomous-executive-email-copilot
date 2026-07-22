// SaaS/account API client. Uses its own session token (distinct from the legacy
// operator API token managed in App.tsx) so the two auth mechanisms never stomp
// each other. The session token is persisted in localStorage and sent as a
// Bearer credential on every account/product call.

const SESSION_KEY = 'saasSession'

export class SaasError extends Error {
  status?: number
  constructor(message: string, status?: number) {
    super(message)
    this.name = 'SaasError'
    this.status = status
  }
}

export interface SaasUser {
  id: string
  org_id: string
  email: string
  full_name: string
  role: 'owner' | 'admin' | 'member'
  status: string
}

export interface Organization {
  id: string
  name: string
  slug: string
  status: string
}

export interface Entitlement {
  plan: string
  seats: number
  seats_used: number
  features: string[]
  status: string
  expires_at: string | null
  is_valid: boolean
}

export interface MailboxConnection {
  id: string
  provider: string
  account_email: string
  status: string
  last_synced_at: string | null
  created_at: string
}

export interface ProviderInfo {
  key: string
  name: string
  available: boolean
}

export interface ProcessedMessage {
  id: string
  provider_message_id: string
  sender: string | null
  subject: string | null
  body_preview: string | null
  sender_role: string | null
  priority_hint: string | null
  risk_tag: string | null
  synced_at: string
}

export interface ProposedAction {
  id: string
  message_id: string
  action_type: string
  content: string | null
  escalate_to: string | null
  label: string | null
  status: string
  requires_approval: boolean
  outcome: string | null
  created_at: string
}

export interface SyncResult {
  connection_id: string
  messages: number
  proposed: number
  auto_executed: number
}

export function getSession(): string | undefined {
  if (typeof localStorage === 'undefined') return undefined
  return localStorage.getItem(SESSION_KEY) || undefined
}

export function setSession(token: string | undefined): void {
  if (typeof localStorage === 'undefined') return
  if (token) localStorage.setItem(SESSION_KEY, token)
  else localStorage.removeItem(SESSION_KEY)
}

async function req<T>(base: string, method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getSession()
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(`${base.replace(/\/+$/, '')}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const data = await res.json()
      if (data && typeof data.detail === 'string') detail = data.detail
    } catch {
      // keep status-based message
    }
    throw new SaasError(detail, res.status)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export interface AuthResult {
  access_token: string
  user: SaasUser
  organization: Organization
}

export function createSaasClient(base: string) {
  return {
    signup: (email: string, password: string, full_name: string, org_name: string) =>
      req<AuthResult>(base, 'POST', '/auth/signup', { email, password, full_name, org_name }),
    login: (email: string, password: string) =>
      req<AuthResult>(base, 'POST', '/auth/login', { email, password }),
    me: () => req<{ user: SaasUser; organization: Organization }>(base, 'GET', '/auth/me'),
    changePassword: (current_password: string, new_password: string) =>
      req<{ status: string }>(base, 'POST', '/auth/change-password', {
        current_password,
        new_password,
      }),
    forgotPassword: (email: string) =>
      req<{ status: string }>(base, 'POST', '/auth/forgot-password', { email }),
    resetPassword: (token: string, new_password: string) =>
      req<{ status: string }>(base, 'POST', '/auth/reset-password', { token, new_password }),
    getOrg: () =>
      req<{ organization: Organization; entitlement: Entitlement; member_count: number }>(
        base,
        'GET',
        '/org',
      ),
    listMembers: () => req<{ members: SaasUser[] }>(base, 'GET', '/org/members'),
    inviteMember: (email: string, full_name: string, role: string, temp_password: string) =>
      req<{ member: SaasUser }>(base, 'POST', '/org/members', {
        email,
        full_name,
        role,
        temp_password,
      }),
    updateMemberRole: (id: string, role: string) =>
      req<{ member: SaasUser }>(base, 'PATCH', `/org/members/${id}/role`, { role }),
    removeMember: (id: string) => req<{ status: string }>(base, 'DELETE', `/org/members/${id}`),
    getEntitlement: () => req<Entitlement>(base, 'GET', '/billing/entitlement'),
    activateLicense: (license_key: string) =>
      req<{ status: string; entitlement: Entitlement }>(base, 'POST', '/billing/activate-license', {
        license_key,
      }),
    listProviders: () => req<{ providers: ProviderInfo[] }>(base, 'GET', '/mailbox/providers'),
    listConnections: () =>
      req<{ connections: MailboxConnection[] }>(base, 'GET', '/mailbox/connections'),
    connectMailbox: (provider: string) =>
      req<{ authorize_url: string }>(base, 'POST', `/mailbox/connect/${provider}`),
    disconnectMailbox: (id: string) =>
      req<{ status: string }>(base, 'DELETE', `/mailbox/connections/${id}`),
    syncInbox: (connection_id?: string) =>
      req<{ results: SyncResult[] }>(base, 'POST', '/inbox/sync', { connection_id }),
    listInboxMessages: () => req<{ messages: ProcessedMessage[] }>(base, 'GET', '/inbox/messages'),
    listInboxActions: (status?: string) =>
      req<{ actions: ProposedAction[] }>(
        base,
        'GET',
        status ? `/inbox/actions?status=${encodeURIComponent(status)}` : '/inbox/actions',
      ),
    approveAction: (id: string) =>
      req<{ action: ProposedAction }>(base, 'POST', `/inbox/actions/${id}/approve`),
    rejectAction: (id: string, comment?: string) =>
      req<{ action: ProposedAction }>(base, 'POST', `/inbox/actions/${id}/reject`, { comment }),
    exportOrg: () => req<Record<string, unknown>>(base, 'GET', '/org/export'),
    deleteOrg: (confirm: string) =>
      req<{ status: string; deleted: Record<string, number> }>(base, 'DELETE', '/org', { confirm }),
  }
}

export type SaasClient = ReturnType<typeof createSaasClient>
