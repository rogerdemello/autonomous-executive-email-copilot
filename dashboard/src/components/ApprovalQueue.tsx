import { useState, useEffect } from 'react'

interface ApprovalRequest {
  id: string
  action_type: string
  email_id: string
  content?: string
  escalate_to?: string
  requested_at: number
}

interface Props {
  apiBase: string
}

function ApprovalQueue({ apiBase }: Props) {
  const [pending, setPending] = useState<ApprovalRequest[]>([])
  const [history, setHistory] = useState<ApprovalRequest[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'pending' | 'history'>('pending')

  const loadPending = async () => {
    try {
      const res = await fetch(`${apiBase}/approval/pending`)
      if (res.ok) {
        const data = await res.json()
        setPending(data)
      }
    } catch (e) {
      console.error('Failed to load pending:', e)
    }
  }

  const loadHistory = async (limit = 20) => {
    try {
      const res = await fetch(`${apiBase}/approval/history?limit=${limit}`)
      if (res.ok) {
        const data = await res.json()
        setHistory(data)
      }
    } catch (e) {
      console.error('Failed to load history:', e)
    }
  }

  useEffect(() => {
    loadPending()
    loadHistory()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleApprove = async (requestId: string) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${apiBase}/approval/${requestId}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approver_id: 'admin', comment: 'Approved via dashboard' }),
      })
      if (!res.ok) throw new Error('Failed to approve')
      await loadPending()
      await loadHistory()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to approve')
    } finally {
      setLoading(false)
    }
  }

  const handleReject = async (requestId: string) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${apiBase}/approval/${requestId}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approver_id: 'admin', comment: 'Rejected via dashboard' }),
      })
      if (!res.ok) throw new Error('Failed to reject')
      await loadPending()
      await loadHistory()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to reject')
    } finally {
      setLoading(false)
    }
  }

  const refresh = () => {
    loadPending()
    loadHistory()
  }

  return (
    <div>
      <div className="metrics">
        <div className="metric">
          <div className="metric-value">{pending.length}</div>
          <div className="metric-label">Pending</div>
        </div>
        <div className="metric">
          <div className="metric-value">{history.filter((h) => h.id).length}</div>
          <div className="metric-label">History</div>
        </div>
      </div>

      <div className="tabs">
        <button
          className={`tab ${activeTab === 'pending' ? 'active' : ''}`}
          onClick={() => setActiveTab('pending')}
        >
          Pending ({pending.length})
        </button>
        <button
          className={`tab ${activeTab === 'history' ? 'active' : ''}`}
          onClick={() => setActiveTab('history')}
        >
          History
        </button>
        <button className="btn" onClick={refresh} style={{ marginLeft: 'auto' }}>
          Refresh
        </button>
      </div>

      {error && (
        <div
          className="card"
          style={{ marginBottom: '1rem', background: '#fee2e2', color: '#991b1b' }}
        >
          {error}
        </div>
      )}

      <div className="card">
        {activeTab === 'pending' && (
          <>
            <h3 style={{ marginBottom: '1rem' }}>Pending Approvals</h3>
            {pending.length === 0 ? (
              <p style={{ color: 'var(--text-muted)' }}>No pending approvals</p>
            ) : (
              pending.map((req) => (
                <div key={req.id} className="approval-item">
                  <div>
                    <strong>ID:</strong> {req.id}
                  </div>
                  <div>
                    <strong>Action:</strong> {req.action_type} | <strong>Email:</strong>{' '}
                    {req.email_id}
                  </div>
                  {req.content && (
                    <div>
                      <strong>Content:</strong> {req.content.slice(0, 100)}...
                    </div>
                  )}
                  {req.escalate_to && (
                    <div>
                      <strong>Escalate to:</strong> {req.escalate_to}
                    </div>
                  )}
                  <div className="approval-actions">
                    <button
                      className="approve"
                      onClick={() => handleApprove(req.id)}
                      disabled={loading}
                    >
                      Approve
                    </button>
                    <button
                      className="reject"
                      onClick={() => handleReject(req.id)}
                      disabled={loading}
                    >
                      Reject
                    </button>
                  </div>
                </div>
              ))
            )}
          </>
        )}

        {activeTab === 'history' && (
          <>
            <h3 style={{ marginBottom: '1rem' }}>Approval History</h3>
            {history.length === 0 ? (
              <p style={{ color: 'var(--text-muted)' }}>No approval history</p>
            ) : (
              history.map((req, idx) => (
                <div key={idx} className="approval-item" style={{ opacity: 0.7 }}>
                  <div>
                    <strong>ID:</strong> {req.id}
                  </div>
                  <div>
                    <strong>Action:</strong> {req.action_type} | <strong>Email:</strong>{' '}
                    {req.email_id}
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                    Requested at: {new Date(req.requested_at * 1000).toLocaleString()}
                  </div>
                </div>
              ))
            )}
          </>
        )}
      </div>
    </div>
  )
}

export default ApprovalQueue
