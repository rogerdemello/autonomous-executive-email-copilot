import { useState } from 'react'

interface Decision {
  step: number
  action: { action_type: string; email_id?: string }
  reason: string
  status: string
  confidence?: number
  latency_ms?: number
  model_name?: string
}

interface Episode {
  episode_id: string
  task_id: string
  seed: number
  persona: string
  steps: number
  score: number
  total_reward: number
  decisions: Decision[]
}

interface Props {
  apiBase: string
}

function Replay({ apiBase }: Props) {
  const [episode, setEpisode] = useState<Episode | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [taskId, setTaskId] = useState('hard_full_management')
  const [seed, setSeed] = useState(42)
  const [persona, setPersona] = useState('balanced')
  const [currentStep, setCurrentStep] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)

  const episodeId = `${taskId}_${seed}_${persona}`

  const loadEpisode = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${apiBase}/replay/${episodeId}`)
      if (!res.ok) {
        if (res.status === 404) {
          throw new Error('Episode not found. Run AI Demo first.')
        }
        throw new Error('Failed to load episode')
      }
      const data = await res.json()
      setEpisode(data)
      setCurrentStep(0)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  const runAndLoad = async () => {
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
      await loadEpisode()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  const play = () => {
    setIsPlaying(true)
  }

  const pause = () => {
    setIsPlaying(false)
  }

  const stepForward = () => {
    if (episode && currentStep < episode.decisions.length - 1) {
      setCurrentStep(currentStep + 1)
    }
  }

  const stepBack = () => {
    if (currentStep > 0) {
      setCurrentStep(currentStep - 1)
    }
  }

  const goToStart = () => {
    setCurrentStep(0)
  }

  const goToEnd = () => {
    if (episode) {
      setCurrentStep(episode.decisions.length - 1)
    }
  }

  const currentDecision = episode?.decisions[currentStep]

  return (
    <div>
      <div className="metrics">
        <div className="metric">
          <div className="metric-value">{episode?.score.toFixed(4) || '-'}</div>
          <div className="metric-label">Score</div>
        </div>
        <div className="metric">
          <div className="metric-value">{episode?.total_reward.toFixed(4) || '-'}</div>
          <div className="metric-label">Total Reward</div>
        </div>
        <div className="metric">
          <div className="metric-value">{episode?.steps || 0}</div>
          <div className="metric-label">Steps</div>
        </div>
        <div className="metric">
          <div className="metric-value">{episode?.decisions.length || 0}</div>
          <div className="metric-label">Decisions</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: '1rem' }}>
        <div
          className="form-group"
          style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '1rem' }}
        >
          <select
            value={taskId}
            onChange={(e) => setTaskId(e.target.value)}
            style={{ width: 'auto' }}
          >
            <option value="easy_classification">easy_classification</option>
            <option value="medium_prioritization">medium_prioritization</option>
            <option value="hard_full_management">hard_full_management</option>
          </select>
          <select
            value={persona}
            onChange={(e) => setPersona(e.target.value)}
            style={{ width: 'auto' }}
          >
            <option value="strict_ceo">strict_ceo</option>
            <option value="balanced">balanced</option>
            <option value="chill_manager">chill_manager</option>
          </select>
          <input
            type="number"
            value={seed}
            onChange={(e) => setSeed(parseInt(e.target.value) || 42)}
            style={{ width: '80px' }}
            placeholder="Seed"
          />
          <button className="btn" onClick={runAndLoad} disabled={loading}>
            {loading ? 'Running...' : 'Run & Load'}
          </button>
          <button className="btn" onClick={loadEpisode} disabled={loading}>
            Load Only
          </button>
        </div>

        {episode && (
          <div className="replay-controls">
            <button onClick={goToStart} disabled={currentStep === 0}>
              |&lt;
            </button>
            <button onClick={stepBack} disabled={currentStep === 0}>
              &lt;
            </button>
            {isPlaying ? (
              <button onClick={pause}>Pause</button>
            ) : (
              <button onClick={play} disabled={currentStep >= episode.decisions.length - 1}>
                Play
              </button>
            )}
            <button onClick={stepForward} disabled={currentStep >= episode.decisions.length - 1}>
              &gt;
            </button>
            <button onClick={goToEnd} disabled={currentStep >= episode.decisions.length - 1}>
              &gt;|
            </button>
            <div className="slider-container">
              <input
                type="range"
                min={0}
                max={episode.decisions.length - 1}
                value={currentStep}
                onChange={(e) => setCurrentStep(parseInt(e.target.value))}
              />
            </div>
            <span style={{ fontSize: '0.75rem' }}>
              {currentStep + 1} / {episode.decisions.length}
            </span>
          </div>
        )}
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
        <h3 style={{ marginBottom: '1rem' }}>Episode Replay</h3>
        {!episode ? (
          <p style={{ color: 'var(--text-muted)' }}>Load an episode to replay</p>
        ) : currentDecision ? (
          <div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '1rem',
              }}
            >
              <strong>
                Step {currentDecision.step}: {currentDecision.action.action_type}
              </strong>
              <span
                className={`status-badge status-${currentDecision.status === 'success' ? 'success' : currentDecision.status === 'fallback' ? 'warning' : 'error'}`}
              >
                {currentDecision.status}
              </span>
            </div>
            <div style={{ marginBottom: '0.5rem' }}>
              <strong>Target:</strong> {currentDecision.action.email_id || 'N/A'}
            </div>
            <div style={{ marginBottom: '0.5rem' }}>
              <strong>Reason:</strong> {currentDecision.reason}
            </div>
            {currentDecision.confidence !== undefined && (
              <div style={{ marginBottom: '0.5rem' }}>
                <strong>Confidence:</strong> {currentDecision.confidence.toFixed(2)}
              </div>
            )}
            {currentDecision.model_name && (
              <div style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>
                Model: {currentDecision.model_name}{' '}
                {currentDecision.latency_ms
                  ? ` | Latency: ${currentDecision.latency_ms.toFixed(0)}ms`
                  : ''}
              </div>
            )}
          </div>
        ) : (
          <p style={{ color: 'var(--text-muted)' }}>No decisions in episode</p>
        )}
      </div>
    </div>
  )
}

export default Replay
