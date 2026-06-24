import { useState, useEffect, useMemo } from 'react'
import { createApiClient } from '../api'
import Card from './ui/Card'
import Badge from './ui/Badge'
import Banner from './ui/Banner'
import Button from './ui/Button'
import StatRow from './ui/StatTile'
import EmptyState from './ui/EmptyState'
import { actionInfo } from '../labels'

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

  const client = useMemo(() => createApiClient(apiBase), [apiBase])

  const loadPending = async () => {
    try {
      setPending(await client.get<ApprovalRequest[]>('/approval/pending'))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load pending approvals')
    }
  }

  const loadHistory = async (limit = 20) => {
    try {
      setHistory(await client.get<ApprovalRequest[]>(`/approval/history?limit=${limit}`))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load history')
    }
  }

  useEffect(() => {
    loadPending()
    loadHistory()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase])

  const decide = async (requestId: string, verb: 'approve' | 'reject') => {
    setLoading(true)
    setError(null)
    try {
      await client.post(`/approval/${requestId}/${verb}`, {
        approver_id: 'admin',
        comment: `${verb === 'approve' ? 'Approved' : 'Rejected'} via dashboard`,
      })
      await Promise.all([loadPending(), loadHistory()])
    } catch (e) {
      setError(e instanceof Error ? e.message : `Failed to ${verb}`)
    } finally {
      setLoading(false)
    }
  }

  const refresh = () => {
    setError(null)
    loadPending()
    loadHistory()
  }

  return (
    <div>
      <StatRow
        stats={[
          { label: 'Waiting on you', value: pending.length, tone: pending.length ? 'warn' : 'ok' },
          { label: 'Recently decided', value: history.length },
        ]}
      />

      <div className="tabs" role="tablist" aria-label="Approval views">
        <button
          type="button"
          role="tab"
          id="approval-tab-pending"
          aria-selected={activeTab === 'pending'}
          aria-controls="approval-panel-pending"
          tabIndex={activeTab === 'pending' ? 0 : -1}
          className={`tab ${activeTab === 'pending' ? 'active' : ''}`}
          onClick={() => setActiveTab('pending')}
        >
          Waiting ({pending.length})
        </button>
        <button
          type="button"
          role="tab"
          id="approval-tab-history"
          aria-selected={activeTab === 'history'}
          aria-controls="approval-panel-history"
          tabIndex={activeTab === 'history' ? 0 : -1}
          className={`tab ${activeTab === 'history' ? 'active' : ''}`}
          onClick={() => setActiveTab('history')}
        >
          History
        </button>
        <Button variant="ghost" size="sm" onClick={refresh} style={{ marginLeft: 'auto' }}>
          Refresh
        </Button>
      </div>

      {error && <Banner kind="error">{error}</Banner>}

      <Card ariaLabel={activeTab === 'pending' ? 'Pending approvals' : 'Approval history'}>
        {activeTab === 'pending' ? (
          <div role="tabpanel" id="approval-panel-pending" aria-labelledby="approval-tab-pending">
            {pending.length === 0 ? (
              <EmptyState
                title="Nothing waiting"
                hint="The copilot has no actions pending your sign-off."
              />
            ) : (
              <ul className="approval-list" aria-label="Pending approvals">
                {pending.map((req) => {
                  const action = actionInfo(req.action_type)
                  return (
                    <li key={req.id} className="approval-item">
                      <div className="approval-item__head">
                        <strong>The copilot wants to {action.label.toLowerCase()}</strong>
                        <Badge tone={action.tone}>{action.label}</Badge>
                      </div>
                      <div className="approval-item__body">
                        <p>
                          <span className="muted">Email</span> {req.email_id}
                        </p>
                        {req.escalate_to && (
                          <p>
                            <span className="muted">Send to</span> {req.escalate_to}
                          </p>
                        )}
                        {req.content && (
                          <p>
                            <span className="muted">Draft</span> “{req.content.slice(0, 140)}
                            {req.content.length > 140 ? '…' : ''}”
                          </p>
                        )}
                      </div>
                      <div className="approval-actions">
                        <Button
                          variant="primary"
                          onClick={() => decide(req.id, 'approve')}
                          disabled={loading}
                          aria-label={`Approve: ${action.label} ${req.email_id}`}
                        >
                          Approve
                        </Button>
                        <Button
                          variant="danger"
                          onClick={() => decide(req.id, 'reject')}
                          disabled={loading}
                          aria-label={`Reject: ${action.label} ${req.email_id}`}
                        >
                          Reject
                        </Button>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        ) : (
          <div role="tabpanel" id="approval-panel-history" aria-labelledby="approval-tab-history">
            {history.length === 0 ? (
              <EmptyState title="No history yet" hint="Decisions you make will be listed here." />
            ) : (
              <ul className="approval-list" aria-label="Approval history">
                {history.map((req, idx) => {
                  const action = actionInfo(req.action_type)
                  return (
                    <li key={req.id || idx} className="approval-item is-history">
                      <div className="approval-item__head">
                        <strong>{action.label}</strong>
                        <Badge tone="neutral">{req.email_id}</Badge>
                      </div>
                      <p className="muted" style={{ fontSize: 'var(--text-xs)' }}>
                        {new Date(req.requested_at * 1000).toLocaleString()}
                      </p>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        )}
      </Card>
    </div>
  )
}

export default ApprovalQueue
