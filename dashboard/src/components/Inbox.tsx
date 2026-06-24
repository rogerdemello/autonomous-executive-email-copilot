import { useState, useEffect, useMemo } from 'react'
import { createApiClient } from '../api'
import Card from './ui/Card'
import Badge from './ui/Badge'
import Banner from './ui/Banner'
import Button from './ui/Button'
import StatRow from './ui/StatTile'
import EmptyState from './ui/EmptyState'
import ScenarioPicker from './ScenarioPicker'
import {
  styleLabel,
  riskLevelTone,
  riskInfo,
  priorityInfo,
  importanceInfo,
  senderRoleLabel,
  deadlineLabel,
} from '../labels'

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

  const client = useMemo(() => createApiClient(apiBase), [apiBase])

  const loadInbox = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await client.post<Observation>('/reset', { task_id: taskId, seed, persona })
      setObs(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadInbox()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const refreshInbox = async () => {
    if (!obs) return
    setLoading(true)
    try {
      const data = await client.post<Observation>('/state')
      setObs(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to refresh')
    } finally {
      setLoading(false)
    }
  }

  const rowClass = (hint: string) =>
    hint === 'urgent' ? 'is-urgent' : hint === 'high' ? 'is-high' : ''

  return (
    <div>
      <StatRow
        stats={[
          { label: 'Emails', value: obs?.emails.length ?? 0 },
          { label: 'Time left', value: `${obs?.time_remaining ?? 0} min` },
          {
            label: 'Overall risk',
            value: obs ? obs.risk_level : '—',
            tone: obs ? riskLevelTone(obs.risk_level) : 'neutral',
          },
          { label: 'Style', value: obs ? styleLabel(obs.persona) : '—' },
        ]}
      />

      <Card title="Start a session" ariaLabel="Session controls">
        <ScenarioPicker
          taskId={taskId}
          persona={persona}
          seed={seed}
          onTaskId={setTaskId}
          onPersona={setPersona}
          onSeed={setSeed}
        >
          <Button variant="primary" onClick={loadInbox} disabled={loading}>
            {loading ? 'Loading…' : 'New session'}
          </Button>
          <Button variant="secondary" onClick={refreshInbox} disabled={loading || !obs}>
            Refresh
          </Button>
        </ScenarioPicker>
      </Card>

      {error && <Banner kind="error">{error}</Banner>}

      <Card title="Inbox" ariaLabel="Inbox">
        {obs && obs.emails.length === 0 ? (
          <EmptyState title="Inbox zero" hint="Nothing waiting in this session." />
        ) : (
          <ul className="email-list" aria-label="Emails">
            {obs?.emails.map((email) => {
              const priority = priorityInfo(email.priority_hint)
              const risk = riskInfo(email.risk_tag)
              const importance = importanceInfo(email.business_value)
              return (
                <li key={email.id} className={`email-item ${rowClass(email.priority_hint)}`}>
                  <div className="email-row">
                    <span className="email-sender">
                      {email.sender}{' '}
                      <span className="email-sender__role">
                        · {senderRoleLabel(email.sender_role)}
                      </span>
                    </span>
                    <span className="muted">{deadlineLabel(email.deadline_minutes)}</span>
                  </div>
                  <div className="email-subject">{email.subject}</div>
                  <div className="email-meta">
                    <Badge tone={priority.tone} dot>
                      {priority.label}
                    </Badge>
                    <Badge tone={importance.tone}>{importance.label}</Badge>
                    {email.risk_tag !== 'none' && (
                      <Badge tone={risk.tone}>Risk: {risk.label}</Badge>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </Card>
    </div>
  )
}

export default Inbox
