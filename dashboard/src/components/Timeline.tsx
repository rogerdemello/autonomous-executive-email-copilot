import { useState, useMemo } from 'react'
import { createApiClient } from '../api'
import Card from './ui/Card'
import Badge from './ui/Badge'
import Banner from './ui/Banner'
import Button from './ui/Button'
import StatRow from './ui/StatTile'
import EmptyState from './ui/EmptyState'
import ScenarioPicker from './ScenarioPicker'
import { actionInfo, decisionStatus } from '../labels'

interface Decision {
  step: number
  action: { action_type: string; email_id?: string }
  reason: string
  status: string
  confidence?: number
  latency_ms?: number
  model_name?: string
}

interface BaselineResponse {
  decision_trace?: Decision[]
}

interface Props {
  apiBase: string
}

function statusTimelineClass(status: string): string {
  const tone = decisionStatus(status).tone
  return tone === 'ok' ? 'is-ok' : tone === 'warn' ? 'is-warn' : 'is-danger'
}

function Timeline({ apiBase }: Props) {
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [taskId, setTaskId] = useState('hard_full_management')
  const [seed, setSeed] = useState(42)
  const [persona, setPersona] = useState('balanced')

  const client = useMemo(() => createApiClient(apiBase), [apiBase])

  const run = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await client.post<BaselineResponse>('/baseline', {
        task_id: taskId,
        seed,
        persona,
        mode: 'llm',
        max_steps: 20,
      })
      setDecisions(data.decision_trace || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  const handled = decisions.filter((d) => d.status === 'success').length
  const safeDefault = decisions.filter((d) => d.status.startsWith('fallback')).length
  const attention = decisions.length - handled - safeDefault

  return (
    <div>
      <StatRow
        stats={[
          { label: 'Steps taken', value: decisions.length },
          { label: 'Handled by copilot', value: handled, tone: 'ok' },
          { label: 'Safe defaults', value: safeDefault, tone: 'warn' },
          { label: 'Needed attention', value: attention, tone: attention ? 'danger' : 'neutral' },
        ]}
      />

      <Card title="Run the copilot" ariaLabel="Run controls">
        <ScenarioPicker
          taskId={taskId}
          persona={persona}
          seed={seed}
          onTaskId={setTaskId}
          onPersona={setPersona}
          onSeed={setSeed}
        >
          <Button variant="primary" onClick={run} disabled={loading}>
            {loading ? 'Running…' : 'Run session'}
          </Button>
        </ScenarioPicker>
      </Card>

      {error && <Banner kind="error">{error}</Banner>}

      <Card title="What the copilot did" ariaLabel="Activity log">
        {decisions.length === 0 ? (
          <EmptyState
            title="No activity yet"
            hint="Run a session to see each step the copilot took."
          />
        ) : (
          <ol className="timeline">
            {decisions.map((decision, idx) => {
              const action = actionInfo(decision.action.action_type)
              const status = decisionStatus(decision.status)
              return (
                <li key={idx} className={`timeline-item ${statusTimelineClass(decision.status)}`}>
                  <div className="timeline-row">
                    <strong>
                      Step {decision.step} · {action.label}
                      {decision.action.email_id ? ` ${decision.action.email_id}` : ''}
                    </strong>
                    <Badge tone={status.tone}>{status.label}</Badge>
                  </div>
                  <p className="timeline-reason">
                    {decision.reason.length > 200
                      ? decision.reason.slice(0, 200) + '…'
                      : decision.reason}
                  </p>
                  {decision.confidence !== undefined && (
                    <p className="timeline-detail">
                      Confidence {Math.round(decision.confidence * 100)}%
                      {decision.latency_ms ? ` · ${decision.latency_ms.toFixed(0)}ms` : ''}
                    </p>
                  )}
                </li>
              )
            })}
          </ol>
        )}
      </Card>
    </div>
  )
}

export default Timeline
