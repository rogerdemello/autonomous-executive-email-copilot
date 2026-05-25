import { useState, useEffect } from 'react'

interface Email {
  id: string
  sender: string
  sender_role: string
  subject: string
  body: string
  priority_hint: string
  deadline_minutes: number
  business_value: number
  risk_tag: string
}

interface Observation {
  emails: Email[]
  time_remaining: number
  pending_actions: string[]
  risk_level: string
  current_minute: number
  persona: string
  remaining_interruptions: number
}

interface Props {
  apiBase: string
}

function Inbox({ apiBase }: Props) {
  const [obs, setObs] = useState<Observation | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [taskId, setTaskId] = useState('hard_full_management')
  const [seed, setSeed] = useState(42)
  const [persona, setPersona] = useState('balanced')

  const loadInbox = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${apiBase}/reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: taskId, seed, persona }),
      })
      if (!res.ok) throw new Error('Failed to load inbox')
      const data = await res.json()
      setObs(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadInbox()
  }, [])

  const refreshInbox = async () => {
    if (!obs) return
    setLoading(true)
    try {
      const res = await fetch(`${apiBase}/state`, { method: 'POST' })
      if (res.ok) {
        const data = await res.json()
        setObs(data)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to refresh')
    } finally {
      setLoading(false)
    }
  }

  const getPriorityClass = (hint: string) => {
    if (hint === 'urgent') return 'urgent'
    if (hint === 'high') return 'high'
    return ''
  }

  return (
    <div>
      <div className="metrics">
        <div className="metric">
          <div className="metric-value">{obs?.emails.length || 0}</div>
          <div className="metric-label">Emails</div>
        </div>
        <div className="metric">
          <div className="metric-value">{obs?.time_remaining || 0}</div>
          <div className="metric-label">Minutes Left</div>
        </div>
        <div className="metric">
          <div className="metric-value">{obs?.risk_level || 'N/A'}</div>
          <div className="metric-label">Risk Level</div>
        </div>
        <div className="metric">
          <div className="metric-value">{obs?.persona || 'N/A'}</div>
          <div className="metric-label">Persona</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: '1rem' }}>
        <div className="form-group" style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          <select value={taskId} onChange={e => setTaskId(e.target.value)} style={{ width: 'auto' }}>
            <option value="easy_classification">easy_classification</option>
            <option value="medium_prioritization">medium_prioritization</option>
            <option value="hard_full_management">hard_full_management</option>
          </select>
          <select value={persona} onChange={e => setPersona(e.target.value)} style={{ width: 'auto' }}>
            <option value="strict_ceo">strict_ceo</option>
            <option value="balanced">balanced</option>
            <option value="chill_manager">chill_manager</option>
          </select>
          <input
            type="number"
            value={seed}
            onChange={e => setSeed(parseInt(e.target.value) || 42)}
            style={{ width: '80px' }}
            placeholder="Seed"
          />
          <button className="btn btn-primary" onClick={loadInbox} disabled={loading}>
            {loading ? 'Loading...' : 'New Episode'}
          </button>
          <button className="btn" onClick={refreshInbox} disabled={loading}>
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="card" style={{ marginBottom: '1rem', background: '#fee2e2', color: '#991b1b' }}>
          {error}
        </div>
      )}

      <div className="card">
        <h3 style={{ marginBottom: '1rem' }}>Inbox</h3>
        {obs?.emails.length === 0 ? (
          <p style={{ color: 'var(--text-muted)' }}>No emails in inbox</p>
        ) : (
          <div className="email-list">
            {obs?.emails.map(email => (
              <div key={email.id} className={`email-item ${getPriorityClass(email.priority_hint)}`}>
                <div className="email-sender">{email.sender} <span style={{ fontWeight: 'normal', color: 'var(--text-muted)' }}>({email.sender_role})</span></div>
                <div className="email-subject">{email.subject}</div>
                <div className="email-meta">
                  Priority: {email.priority_hint} | Deadline: {email.deadline_minutes}m | Value: {email.business_value} | Risk: {email.risk_tag}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default Inbox