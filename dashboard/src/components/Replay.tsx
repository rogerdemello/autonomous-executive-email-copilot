import { useState, useMemo } from 'react'
import { createApiClient, ApiError } from '../api'
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

  const client = useMemo(() => createApiClient(apiBase), [apiBase])
  const episodeId = `${taskId}_${seed}_${persona}`

  const loadEpisode = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await client.get<Episode>(`/replay/${episodeId}`)
      setEpisode(data)
      setCurrentStep(0)
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setError(
          'No saved session for this combination yet. Run one from the Activity screen first.',
        )
      } else {
        setError(e instanceof Error ? e.message : 'Unknown error')
      }
    } finally {
      setLoading(false)
    }
  }

  const runAndLoad = async () => {
    setLoading(true)
    setError(null)
    try {
      await client.post('/baseline', {
        task_id: taskId,
        seed,
        persona,
        mode: 'llm',
        max_steps: 20,
      })
      await loadEpisode()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  const lastIndex = episode ? episode.decisions.length - 1 : 0
  const stepForward = () => setCurrentStep((s) => Math.min(s + 1, lastIndex))
  const stepBack = () => setCurrentStep((s) => Math.max(s - 1, 0))
  const goToStart = () => setCurrentStep(0)
  const goToEnd = () => setCurrentStep(lastIndex)

  const currentDecision = episode?.decisions[currentStep]
  const action = currentDecision ? actionInfo(currentDecision.action.action_type) : null
  const status = currentDecision ? decisionStatus(currentDecision.status) : null

  return (
    <div>
      <StatRow
        stats={[
          {
            label: 'Quality score',
            value: episode ? `${Math.round(episode.score * 100)}%` : '—',
            tone: 'accent',
          },
          { label: 'Steps', value: episode?.steps ?? 0 },
          { label: 'Decisions', value: episode?.decisions.length ?? 0 },
        ]}
      />

      <Card title="Review a past session" ariaLabel="Replay controls">
        <ScenarioPicker
          taskId={taskId}
          persona={persona}
          seed={seed}
          onTaskId={setTaskId}
          onPersona={setPersona}
          onSeed={setSeed}
        >
          <Button variant="primary" onClick={runAndLoad} disabled={loading}>
            {loading ? 'Working…' : 'Run & review'}
          </Button>
          <Button variant="secondary" onClick={loadEpisode} disabled={loading}>
            Load saved
          </Button>
        </ScenarioPicker>

        {episode && episode.decisions.length > 0 && (
          <div className="replay-controls" role="group" aria-label="Playback controls">
            <Button
              size="sm"
              onClick={goToStart}
              disabled={currentStep === 0}
              aria-label="First step"
            >
              <span aria-hidden="true">|‹</span>
            </Button>
            <Button
              size="sm"
              onClick={stepBack}
              disabled={currentStep === 0}
              aria-label="Previous step"
            >
              <span aria-hidden="true">‹</span>
            </Button>
            <Button
              size="sm"
              onClick={stepForward}
              disabled={currentStep >= lastIndex}
              aria-label="Next step"
            >
              <span aria-hidden="true">›</span>
            </Button>
            <Button
              size="sm"
              onClick={goToEnd}
              disabled={currentStep >= lastIndex}
              aria-label="Last step"
            >
              <span aria-hidden="true">›|</span>
            </Button>
            <div className="slider-container">
              <input
                type="range"
                min={0}
                max={lastIndex}
                value={currentStep}
                onChange={(e) => setCurrentStep(parseInt(e.target.value))}
                aria-label="Step position"
                aria-valuetext={`Step ${currentStep + 1} of ${episode.decisions.length}`}
              />
            </div>
            <span className="replay-counter">
              {currentStep + 1} / {episode.decisions.length}
            </span>
          </div>
        )}
      </Card>

      {error && <Banner kind="error">{error}</Banner>}

      <Card title="Session step" ariaLabel="Session step" className="" as="section">
        <div aria-live="polite">
          {!episode ? (
            <EmptyState
              title="No session loaded"
              hint="Run or load a session to step through it."
            />
          ) : currentDecision && action && status ? (
            <div>
              <div className="approval-item__head">
                <strong>
                  Step {currentDecision.step} · {action.label}
                </strong>
                <Badge tone={status.tone}>{status.label}</Badge>
              </div>
              {currentDecision.action.email_id && (
                <p className="detail-line">
                  <span className="detail-line__label">Email</span>
                  {currentDecision.action.email_id}
                </p>
              )}
              <p className="detail-line">
                <span className="detail-line__label">Why</span>
                {currentDecision.reason}
              </p>
              {currentDecision.confidence !== undefined && (
                <p className="detail-line muted">
                  Confidence {Math.round(currentDecision.confidence * 100)}%
                  {currentDecision.latency_ms
                    ? ` · ${currentDecision.latency_ms.toFixed(0)}ms`
                    : ''}
                </p>
              )}
            </div>
          ) : (
            <EmptyState title="No steps in this session" />
          )}
        </div>
      </Card>
    </div>
  )
}

export default Replay
