import { useState, useEffect } from 'react'

interface Decision {
  step: number
  action: { action_type: string; email_id?: string }
  reason: string
  status: string
  confidence?: number
  latency_ms?: number
  model_name?: string
}

interface Props {
  apiBase: string
}

function Timeline({ apiBase }: Props) {
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [taskId, setTaskId] = useState('hard_full_management')
  const [seed, setSeed] = useState(42)
  const [persona, setPersona] = useState('balanced')

  const runBaseline = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${apiBase}/baseline`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: taskId,
          seed,
          persona,
          mode: 'llm',
          max_steps: 20,
        }),
      })
      if (!res.ok) throw new Error('Failed to run baseline')
      const data = await res.json()
      setDecisions(data.decision_trace || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  const getStatusClass = (status: string) => {
    if (status === 'success') return 'success'
    if (status === 'fallback') return 'warning'
    return 'error'
  }

  return (
    <div>
      <div className="metrics">
        <div className="metric">
          <div className="metric-value">{decisions.length}</div>
          <div className="metric-label">Decisions</div>
        </div>
        <div className="metric">
          <div className="metric-value">
            {decisions.filter(d => d.status === 'success').length}
          </div>
          <div className="metric-label">AI Mode</div>
        </div>
        <div className="metric">
          <div className="metric-value">
            {decisions.filter(d => d.status === 'fallback').length}
          </div>
          <div className="metric-label">Fallback</div>
        </div>
        <div className="metric">
          <div className="metric-value">
            {decisions.filter(d => d.status === 'error').length}
          </div>
          <div className="metric-label">Errors</div>
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
          <button className="btn btn-primary" onClick={runBaseline} disabled={loading}>
            {loading ? 'Running...' : 'Run AI Demo'}
          </button>
        </div>
      </div>

      {error && (
        <div className="card" style={{ marginBottom: '1rem', background: '#fee2e2', color: '#991b1b' }}>
          {error}
        </div>
      )}

      <div className="card">
        <h3 style={{ marginBottom: '1rem' }}>Decision Timeline</h3>
        {decisions.length === 0 ? (
          <p style={{ color: 'var(--text-muted)' }}>Run AI Demo to see timeline</p>
        ) : (
          <div className="timeline">
            {decisions.map((decision, idx) => (
              <div key={idx} className={`timeline-item ${getStatusClass(decision.status)}`}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong>Step {decision.step}: {decision.action.action_type}</strong>
                  <span className={`status-badge status-${getStatusClass(decision.status)}`}>
                    {decision.status}
                  </span>
                </div>
                <div style={{ marginTop: '0.5rem', fontSize: '0.875rem', color: 'var(--text-muted)' }}>
                  Target: {decision.action.email_id || 'N/A'}
                </div>
                <div style={{ marginTop: '0.5rem' }}>
                  {decision.reason.length > 200 ? decision.reason.slice(0, 200) + '...' : decision.reason}
                </div>
                {decision.confidence !== undefined && (
                  <div style={{ marginTop: '0.5rem' }}>
                    <span style={{ fontSize: '0.75rem' }}>Confidence: {decision.confidence.toFixed(2)}</span>
                  </div>
                )}
                {decision.model_name && (
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                    Model: {decision.model_name} {decision.latency_ms ? `| ${decision.latency_ms.toFixed(0)}ms` : ''}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default Timeline