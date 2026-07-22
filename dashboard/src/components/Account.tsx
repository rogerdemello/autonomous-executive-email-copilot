import { useCallback, useEffect, useMemo, useState } from 'react'
import Badge from './ui/Badge'
import Banner from './ui/Banner'
import Button from './ui/Button'
import Card from './ui/Card'
import EmptyState from './ui/EmptyState'
import Field from './ui/Field'
import StatRow from './ui/StatTile'
import {
  createSaasClient,
  getSession,
  setSession,
  type AuditEntry,
  type Entitlement,
  type MailboxConnection,
  type Organization,
  type ProcessedMessage,
  type ProposedAction,
  type ProviderInfo,
  type SaasUser,
} from '../saas/client'

interface Props {
  apiBase: string
}

// --- Auth (login / signup) --------------------------------------------------
function AuthForms({
  apiBase,
  onAuthed,
}: {
  apiBase: string
  onAuthed: (token: string, user: SaasUser, org: Organization) => void
}) {
  const client = useMemo(() => createSaasClient(apiBase), [apiBase])
  const [mode, setMode] = useState<'login' | 'signup' | 'forgot'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [orgName, setOrgName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [sent, setSent] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      if (mode === 'forgot') {
        await client.forgotPassword(email)
        setSent(true)
        return
      }
      const res =
        mode === 'login'
          ? await client.login(email, password)
          : await client.signup(email, password, fullName, orgName)
      onAuthed(res.access_token, res.user, res.organization)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
    } finally {
      setBusy(false)
    }
  }

  const title =
    mode === 'login'
      ? 'Sign in'
      : mode === 'signup'
        ? 'Create your workspace'
        : 'Reset your password'

  if (mode === 'forgot' && sent) {
    return (
      <Card title="Check your email">
        <Banner kind="success">
          If an account exists for {email}, we've sent a password reset link.
        </Banner>
        <Button
          variant="ghost"
          onClick={() => {
            setMode('login')
            setSent(false)
          }}
        >
          Back to sign in
        </Button>
      </Card>
    )
  }

  return (
    <Card title={title}>
      <form onSubmit={submit} className="stack">
        {mode === 'signup' && (
          <Field label="Organization name">
            {(id) => (
              <input
                id={id}
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                placeholder="Acme Inc"
                required
              />
            )}
          </Field>
        )}
        {mode === 'signup' && (
          <Field label="Your name">
            {(id) => (
              <input
                id={id}
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="Alex Vance"
              />
            )}
          </Field>
        )}
        <Field label="Work email">
          {(id) => (
            <input
              id={id}
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              autoComplete="username"
              required
            />
          )}
        </Field>
        {mode !== 'forgot' && (
          <Field label="Password" hint={mode === 'signup' ? 'At least 8 characters' : undefined}>
            {(id) => (
              <input
                id={id}
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                required
              />
            )}
          </Field>
        )}
        {error && <Banner kind="error">{error}</Banner>}
        <div className="row">
          <Button type="submit" variant="primary" disabled={busy}>
            {busy
              ? 'Please wait…'
              : mode === 'login'
                ? 'Sign in'
                : mode === 'signup'
                  ? 'Start free trial'
                  : 'Send reset link'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setMode(mode === 'signup' ? 'login' : 'signup')
              setError('')
            }}
          >
            {mode === 'signup' ? 'Have an account? Sign in' : 'Need an account? Sign up'}
          </Button>
        </div>
        {mode === 'login' && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setMode('forgot')
              setError('')
            }}
          >
            Forgot password?
          </Button>
        )}
        {mode === 'forgot' && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setMode('login')
              setError('')
            }}
          >
            Back to sign in
          </Button>
        )}
      </form>
    </Card>
  )
}

// --- Reset password (from an emailed ?reset_token= link) --------------------
function ResetPassword({
  apiBase,
  token,
  onDone,
}: {
  apiBase: string
  token: string
  onDone: () => void
}) {
  const client = useMemo(() => createSaasClient(apiBase), [apiBase])
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    try {
      await client.resetPassword(token, password)
      setDone(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reset failed')
    }
  }

  if (done) {
    return (
      <Card title="Password updated">
        <Banner kind="success">Your password has been reset. You can sign in now.</Banner>
        <Button variant="primary" onClick={onDone}>
          Continue to sign in
        </Button>
      </Card>
    )
  }

  return (
    <Card title="Choose a new password">
      <form onSubmit={submit} className="stack">
        <Field label="New password" hint="At least 8 characters">
          {(id) => (
            <input
              id={id}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              required
            />
          )}
        </Field>
        {error && <Banner kind="error">{error}</Banner>}
        <Button type="submit" variant="primary">
          Set new password
        </Button>
      </form>
    </Card>
  )
}

// --- Members ----------------------------------------------------------------
function Members({
  apiBase,
  me,
  onSeatsChanged,
}: {
  apiBase: string
  me: SaasUser
  onSeatsChanged: () => void
}) {
  const client = useMemo(() => createSaasClient(apiBase), [apiBase])
  const [members, setMembers] = useState<SaasUser[]>([])
  const [error, setError] = useState('')
  const [invite, setInvite] = useState({ email: '', full_name: '', role: 'member', temp: '' })
  const canManage = me.role === 'owner' || me.role === 'admin'

  const load = useCallback(async () => {
    try {
      setMembers((await client.listMembers()).members)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load members')
    }
  }, [client])

  useEffect(() => {
    load()
  }, [load])

  async function doInvite(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    try {
      await client.inviteMember(invite.email, invite.full_name, invite.role, invite.temp)
      setInvite({ email: '', full_name: '', role: 'member', temp: '' })
      await load()
      onSeatsChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Invite failed')
    }
  }

  async function remove(id: string) {
    setError('')
    try {
      await client.removeMember(id)
      await load()
      onSeatsChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Remove failed')
    }
  }

  async function changeRole(id: string, role: string) {
    setError('')
    try {
      await client.updateMemberRole(id, role)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Role change failed')
    }
  }

  return (
    <Card title="Team members">
      {error && <Banner kind="error">{error}</Banner>}
      {members.length === 0 ? (
        <EmptyState title="No members yet" />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Email</th>
              <th>Name</th>
              <th>Role</th>
              {canManage && <th aria-label="Actions" />}
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id}>
                <td>{m.email}</td>
                <td>{m.full_name || '—'}</td>
                <td>
                  {canManage && m.id !== me.id ? (
                    <select value={m.role} onChange={(e) => changeRole(m.id, e.target.value)}>
                      <option value="member">member</option>
                      <option value="admin">admin</option>
                      <option value="owner">owner</option>
                    </select>
                  ) : (
                    <Badge tone={m.role === 'owner' ? 'accent' : 'neutral'}>{m.role}</Badge>
                  )}
                </td>
                {canManage && (
                  <td>
                    {m.id !== me.id && (
                      <Button size="sm" variant="danger" onClick={() => remove(m.id)}>
                        Remove
                      </Button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {canManage && (
        <form onSubmit={doInvite} className="stack" style={{ marginTop: '1rem' }}>
          <h4>Invite a member</h4>
          <div className="row">
            <Field label="Email">
              {(id) => (
                <input
                  id={id}
                  type="email"
                  value={invite.email}
                  onChange={(e) => setInvite({ ...invite, email: e.target.value })}
                  required
                />
              )}
            </Field>
            <Field label="Name">
              {(id) => (
                <input
                  id={id}
                  value={invite.full_name}
                  onChange={(e) => setInvite({ ...invite, full_name: e.target.value })}
                />
              )}
            </Field>
          </div>
          <div className="row">
            <Field label="Role">
              {(id) => (
                <select
                  id={id}
                  value={invite.role}
                  onChange={(e) => setInvite({ ...invite, role: e.target.value })}
                >
                  <option value="member">member</option>
                  <option value="admin">admin</option>
                  {me.role === 'owner' && <option value="owner">owner</option>}
                </select>
              )}
            </Field>
            <Field label="Temporary password" hint="They change it on first sign-in">
              {(id) => (
                <input
                  id={id}
                  type="text"
                  value={invite.temp}
                  onChange={(e) => setInvite({ ...invite, temp: e.target.value })}
                  minLength={8}
                  required
                />
              )}
            </Field>
          </div>
          <Button type="submit" variant="primary">
            Send invite
          </Button>
        </form>
      )}
    </Card>
  )
}

// --- Mailboxes --------------------------------------------------------------
function Mailboxes({ apiBase, me }: { apiBase: string; me: SaasUser }) {
  const client = useMemo(() => createSaasClient(apiBase), [apiBase])
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [connections, setConnections] = useState<MailboxConnection[]>([])
  const [error, setError] = useState('')
  const canManage = me.role === 'owner' || me.role === 'admin'

  const load = useCallback(async () => {
    try {
      const [p, c] = await Promise.all([client.listProviders(), client.listConnections()])
      setProviders(p.providers)
      setConnections(c.connections)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load mailboxes')
    }
  }, [client])

  useEffect(() => {
    load()
  }, [load])

  async function connect(provider: string) {
    setError('')
    try {
      const { authorize_url } = await client.connectMailbox(provider)
      window.location.href = authorize_url
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start connection')
    }
  }

  async function disconnect(id: string) {
    setError('')
    try {
      await client.disconnectMailbox(id)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Disconnect failed')
    }
  }

  return (
    <Card title="Connected mailboxes">
      {error && <Banner kind="error">{error}</Banner>}
      {connections.length === 0 ? (
        <EmptyState
          title="No mailboxes connected"
          hint="Connect a mailbox to let the copilot triage a real inbox."
        />
      ) : (
        <ul className="list">
          {connections.map((c) => (
            <li key={c.id} className="list__item">
              <span>
                <strong>{c.account_email}</strong> <Badge tone="neutral">{c.provider}</Badge>{' '}
                <Badge tone={c.status === 'connected' ? 'ok' : 'warn'}>{c.status}</Badge>
                {c.last_synced_at && (
                  <span className="muted" style={{ marginLeft: 8, fontSize: '0.85em' }}>
                    synced {new Date(c.last_synced_at).toLocaleString()}
                  </span>
                )}
              </span>
              {canManage && (
                <Button size="sm" variant="danger" onClick={() => disconnect(c.id)}>
                  Disconnect
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}

      {canManage && (
        <div className="row" style={{ marginTop: '1rem' }}>
          {providers.map((p) => (
            <Button
              key={p.key}
              variant="secondary"
              disabled={!p.available}
              title={p.available ? '' : 'Not configured on this server'}
              onClick={() => connect(p.key)}
            >
              Connect {p.name}
              {!p.available && ' (unavailable)'}
            </Button>
          ))}
        </div>
      )}
    </Card>
  )
}

// --- Billing ----------------------------------------------------------------
function Billing({
  apiBase,
  me,
  entitlement,
  onChanged,
}: {
  apiBase: string
  me: SaasUser
  entitlement: Entitlement | null
  onChanged: () => void
}) {
  const client = useMemo(() => createSaasClient(apiBase), [apiBase])
  const [key, setKey] = useState('')
  const [error, setError] = useState('')
  const [ok, setOk] = useState('')

  async function activate(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setOk('')
    try {
      const res = await client.activateLicense(key.trim())
      setOk(`Activated ${res.entitlement.plan} plan (${res.entitlement.seats} seats).`)
      setKey('')
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Activation failed')
    }
  }

  return (
    <Card title="Plan & billing">
      {entitlement && (
        <StatRow
          stats={[
            { label: 'Plan', value: entitlement.plan, tone: 'accent' },
            { label: 'Seats used', value: `${entitlement.seats_used} / ${entitlement.seats}` },
            {
              label: 'Status',
              value: entitlement.is_valid ? 'Active' : entitlement.status,
              tone: entitlement.is_valid ? 'ok' : 'warn',
            },
          ]}
        />
      )}
      {entitlement && entitlement.features.length > 0 && (
        <p style={{ marginTop: '0.75rem' }}>
          {entitlement.features.map((f) => (
            <Badge key={f} tone="neutral">
              {f}
            </Badge>
          ))}
        </p>
      )}
      {me.role === 'owner' ? (
        <form onSubmit={activate} className="stack" style={{ marginTop: '1rem' }}>
          <Field
            label="Activate a license key"
            hint="Issued by sales after your contract is signed"
          >
            {(id) => (
              <input
                id={id}
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="Paste license key"
              />
            )}
          </Field>
          {error && <Banner kind="error">{error}</Banner>}
          {ok && <Banner kind="success">{ok}</Banner>}
          <Button type="submit" variant="primary" disabled={!key.trim()}>
            Activate license
          </Button>
        </form>
      ) : (
        <p className="muted" style={{ marginTop: '0.75rem' }}>
          Only an owner can change billing.
        </p>
      )}
    </Card>
  )
}

// --- Inbox review -----------------------------------------------------------
function InboxReview({ apiBase, me }: { apiBase: string; me: SaasUser }) {
  const client = useMemo(() => createSaasClient(apiBase), [apiBase])
  const [messages, setMessages] = useState<ProcessedMessage[]>([])
  const [actions, setActions] = useState<ProposedAction[]>([])
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const canManage = me.role === 'owner' || me.role === 'admin'

  const load = useCallback(async () => {
    try {
      const [m, a] = await Promise.all([client.listInboxMessages(), client.listInboxActions()])
      setMessages(m.messages)
      setActions(a.actions)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load inbox')
    }
  }, [client])

  useEffect(() => {
    load()
  }, [load])

  async function sync() {
    setBusy(true)
    setError('')
    setNote('')
    try {
      const { results } = await client.syncInbox()
      const totals = results.reduce(
        (acc, r) => ({
          messages: acc.messages + r.messages,
          proposed: acc.proposed + r.proposed,
          auto: acc.auto + r.auto_executed,
        }),
        { messages: 0, proposed: 0, auto: 0 },
      )
      setNote(
        `Synced ${totals.messages} messages — ${totals.proposed} awaiting approval, ${totals.auto} auto-handled.`,
      )
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sync failed')
    } finally {
      setBusy(false)
    }
  }

  async function decide(id: string, kind: 'approve' | 'reject') {
    setError('')
    try {
      if (kind === 'approve') await client.approveAction(id)
      else await client.rejectAction(id)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed')
    }
  }

  const proposed = actions.filter((a) => a.status === 'proposed')
  const bySubject = new Map(messages.map((m) => [m.id, m.subject ?? m.provider_message_id]))
  const actionTone = (t: string) => (t === 'escalate' ? 'danger' : t === 'reply' ? 'ok' : 'neutral')

  return (
    <Card
      title="Inbox review"
      actions={
        canManage ? (
          <Button variant="primary" size="sm" onClick={sync} disabled={busy}>
            {busy ? 'Syncing…' : 'Sync now'}
          </Button>
        ) : undefined
      }
    >
      {error && <Banner kind="error">{error}</Banner>}
      {note && <Banner kind="success">{note}</Banner>}

      {proposed.length === 0 ? (
        <EmptyState
          title="Nothing awaiting approval"
          hint={
            messages.length
              ? 'The copilot has handled the current inbox.'
              : 'Connect a mailbox and press “Sync now” to let the copilot work the inbox.'
          }
        />
      ) : (
        <ul className="list">
          {proposed.map((a) => (
            <li key={a.id} className="list__item">
              <span>
                <Badge tone={actionTone(a.action_type)}>{a.action_type}</Badge>{' '}
                {bySubject.get(a.message_id) || a.message_id}
                {a.escalate_to && <span className="muted"> → {a.escalate_to}</span>}
              </span>
              {canManage && (
                <span className="row">
                  <Button size="sm" variant="primary" onClick={() => decide(a.id, 'approve')}>
                    Approve
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => decide(a.id, 'reject')}>
                    Reject
                  </Button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {messages.length > 0 && (
        <p className="muted" style={{ marginTop: '0.75rem', fontSize: '0.9em' }}>
          {messages.length} messages processed · {actions.length} total actions
        </p>
      )}
    </Card>
  )
}

// --- Audit log (admin+) -----------------------------------------------------
function AuditLog({ apiBase }: { apiBase: string }) {
  const client = useMemo(() => createSaasClient(apiBase), [apiBase])
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    client
      .listAuditLog()
      .then((r) => setEntries(r.entries))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load audit log'))
  }, [client])

  return (
    <Card title="Audit log">
      {error && <Banner kind="error">{error}</Banner>}
      {entries.length === 0 ? (
        <EmptyState title="No activity yet" />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>When</th>
              <th>Action</th>
              <th>Target</th>
              <th>IP</th>
            </tr>
          </thead>
          <tbody>
            {entries.slice(0, 50).map((e) => (
              <tr key={e.id}>
                <td>{new Date(e.created_at).toLocaleString()}</td>
                <td>
                  <Badge tone="neutral">{e.action}</Badge>
                </td>
                <td>{e.target || '—'}</td>
                <td>{e.ip || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

// --- Danger zone (owner: export / delete org) -------------------------------
function DangerZone({
  apiBase,
  org,
  onDeleted,
}: {
  apiBase: string
  org: Organization
  onDeleted: () => void
}) {
  const client = useMemo(() => createSaasClient(apiBase), [apiBase])
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function exportData() {
    setError('')
    try {
      const bundle = await client.exportOrg()
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${org.slug}-export.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed')
    }
  }

  async function deleteOrg() {
    setError('')
    setBusy(true)
    try {
      await client.deleteOrg(confirm)
      onDeleted()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
      setBusy(false)
    }
  }

  return (
    <Card title="Data & danger zone">
      <p className="muted">
        Export a full copy of your organization's data, or permanently delete the organization and
        everything in it.
      </p>
      {error && <Banner kind="error">{error}</Banner>}
      <div className="row" style={{ marginBottom: '1rem' }}>
        <Button variant="secondary" onClick={exportData}>
          Export all data (JSON)
        </Button>
      </div>
      <Field label={`Delete organization — type “${org.slug}” to confirm`}>
        {(id) => (
          <input
            id={id}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder={org.slug}
          />
        )}
      </Field>
      <Button variant="danger" disabled={busy || confirm !== org.slug} onClick={deleteOrg}>
        {busy ? 'Deleting…' : 'Permanently delete organization'}
      </Button>
    </Card>
  )
}

// --- Root -------------------------------------------------------------------
function Account({ apiBase }: Props) {
  const client = useMemo(() => createSaasClient(apiBase), [apiBase])
  const [user, setUser] = useState<SaasUser | null>(null)
  const [org, setOrg] = useState<Organization | null>(null)
  const [entitlement, setEntitlement] = useState<Entitlement | null>(null)
  const [loading, setLoading] = useState(true)
  const [resetToken, setResetToken] = useState<string | null>(() =>
    typeof window === 'undefined'
      ? null
      : new URLSearchParams(window.location.search).get('reset_token'),
  )

  const refreshOrg = useCallback(async () => {
    try {
      const data = await client.getOrg()
      setOrg(data.organization)
      setEntitlement(data.entitlement)
    } catch {
      // entitlement is best-effort in the header
    }
  }, [client])

  const bootstrap = useCallback(async () => {
    setLoading(true)
    if (!getSession()) {
      setUser(null)
      setLoading(false)
      return
    }
    try {
      const { user: u, organization } = await client.me()
      setUser(u)
      setOrg(organization)
      await refreshOrg()
    } catch {
      setSession(undefined)
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [client, refreshOrg])

  useEffect(() => {
    bootstrap()
  }, [bootstrap])

  function signOut() {
    setSession(undefined)
    setUser(null)
    setOrg(null)
    setEntitlement(null)
  }

  if (loading) return <p className="muted">Loading account…</p>

  if (!user && resetToken) {
    return (
      <ResetPassword
        apiBase={apiBase}
        token={resetToken}
        onDone={() => {
          setResetToken(null)
          if (typeof window !== 'undefined') {
            window.history.replaceState({}, '', window.location.pathname)
          }
        }}
      />
    )
  }

  if (!user) {
    return (
      <AuthForms
        apiBase={apiBase}
        onAuthed={(token, u, o) => {
          setSession(token)
          setUser(u)
          setOrg(o)
          refreshOrg()
        }}
      />
    )
  }

  return (
    <div className="stack">
      <Card
        title={org?.name ?? 'Workspace'}
        actions={
          <span className="row">
            <Badge tone="accent">{user.role}</Badge>
            <Button size="sm" variant="ghost" onClick={signOut}>
              Sign out
            </Button>
          </span>
        }
      >
        <p className="muted">
          Signed in as <strong>{user.email}</strong>
        </p>
      </Card>
      <Billing apiBase={apiBase} me={user} entitlement={entitlement} onChanged={refreshOrg} />
      <Mailboxes apiBase={apiBase} me={user} />
      <InboxReview apiBase={apiBase} me={user} />
      <Members apiBase={apiBase} me={user} onSeatsChanged={refreshOrg} />
      {(user.role === 'owner' || user.role === 'admin') && <AuditLog apiBase={apiBase} />}
      {user.role === 'owner' && org && (
        <DangerZone apiBase={apiBase} org={org} onDeleted={signOut} />
      )}
    </div>
  )
}

export default Account
