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
  actionInfo,
  scoreVerdict,
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

interface TraceAction {
  action_type: string
  email_id: string | null
}

interface RunResult {
  score: number
  steps: number
  action_trace: TraceAction[]
}

interface Props {
  apiBase: string
}

function Inbox({ apiBase }: Props) {
  const [obs, setObs] = useState<Observation | null>(null)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<RunResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [taskId, setTaskId] = useState('hard_full_management')
  const [seed, setSeed] = useState(42)
  const [persona, setPersona] = useState('balanced')

  const client = useMemo(() => createApiClient(apiBase), [apiBase])

  const loadInbox = async () => {
    setLoading(true)
    setError(null)
    setResult(null)
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

  // One-click: let the copilot work the whole inbox, then show what it did.
  const runCopilot = async () => {
    setRunning(true)
    setError(null)
    try {
      const data = await client.post<RunResult>('/baseline', {
        task_id: taskId,
        seed,
        persona,
        mode: 'baseline',
        max_steps: 100,
      })
      setResult(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'The copilot could not finish this session')
    } finally {
      setRunning(false)
    }
  }

  // email_id -> the action the copilot took on it (last action wins).
  const handledBy = useMemo(() => {
    const map = new Map<string, string>()
    for (const a of result?.action_trace ?? []) {
      if (a.email_id) map.set(a.email_id, a.action_type)
    }
    return map
  }, [result])

  // Plain-English tally of what the copilot did, e.g. "Replied to 8 · Escalated 2".
  const actionSummary = useMemo(() => {
    const counts = new Map<string, number>()
    for (const a of result?.action_trace ?? []) {
      counts.set(a.action_type, (counts.get(a.action_type) ?? 0) + 1)
    }
    return [...counts.entries()].map(([type, n]) => `${actionInfo(type).label} ${n}`).join(' · ')
  }, [result])

  const rowClass = (hint: string) =>
    hint === 'urgent' ? 'is-urgent' : hint === 'high' ? 'is-high' : ''

  const verdict = result ? scoreVerdict(result.score) : null

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
          <Button variant="primary" onClick={runCopilot} disabled={running || loading || !obs}>
            {running ? 'Working…' : 'Let the copilot work'}
          </Button>
          <Button variant="secondary" onClick={loadInbox} disabled={loading || running}>
            {loading ? 'Loading…' : 'New inbox'}
          </Button>
        </ScenarioPicker>
      </Card>

      {error && <Banner kind="error">{error}</Banner>}

      {result && verdict && (
        <Card title="What the copilot did" ariaLabel="Copilot result">
          <div className="result-summary">
            <Badge tone={verdict.tone} dot>
              {verdict.label}
            </Badge>
            <span className="muted">
              Handled {handledBy.size} of {obs?.emails.length ?? 0} emails in {result.steps} steps
            </span>
          </div>
          {actionSummary && <p className="result-actions">{actionSummary}</p>}
        </Card>
      )}

      <Card title="Inbox" ariaLabel="Inbox">
        {obs && obs.emails.length === 0 ? (
          <EmptyState title="Inbox zero" hint="Nothing waiting in this session." />
        ) : (
          <ul className="email-list" aria-label="Emails">
            {obs?.emails.map((email) => {
              const priority = priorityInfo(email.priority_hint)
              const risk = riskInfo(email.risk_tag)
              const importance = importanceInfo(email.business_value)
              const handledAction = handledBy.get(email.id)
              const handled = handledAction ? actionInfo(handledAction) : null
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
                    {handled && (
                      <Badge tone="ok">
                        <span aria-hidden="true">✓ </span>
                        {handled.label}
                      </Badge>
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
