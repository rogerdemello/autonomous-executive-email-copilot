import Field from './ui/Field'
import { WORKLOADS, MANAGEMENT_STYLES } from '../labels'

interface Props {
  taskId: string
  persona: string
  seed: number
  onTaskId: (v: string) => void
  onPersona: (v: string) => void
  onSeed: (v: number) => void
  /** Action buttons rendered inline with the primary controls. */
  children?: React.ReactNode
}

/**
 * Shared workload / management-style picker used by Inbox, Activity and Replay.
 * Plain-English labels up front; the technical "variation" (seed) is tucked
 * into an Advanced disclosure so it never distracts a business reader.
 */
function ScenarioPicker({ taskId, persona, seed, onTaskId, onPersona, onSeed, children }: Props) {
  return (
    <div>
      <div className="toolbar">
        <Field label="Workload">
          {(id) => (
            <select
              id={id}
              className="select"
              value={taskId}
              onChange={(e) => onTaskId(e.target.value)}
            >
              {WORKLOADS.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.label}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Management style">
          {(id) => (
            <select
              id={id}
              className="select"
              value={persona}
              onChange={(e) => onPersona(e.target.value)}
            >
              {MANAGEMENT_STYLES.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          )}
        </Field>
        {children}
      </div>
      <details className="disclosure">
        <summary>Advanced</summary>
        <div className="disclosure__body">
          <Field label="Variation" hint="Same number reproduces the same inbox">
            {(id) => (
              <input
                id={id}
                className="input input--narrow"
                type="number"
                value={seed}
                onChange={(e) => onSeed(parseInt(e.target.value) || 0)}
              />
            )}
          </Field>
        </div>
      </details>
    </div>
  )
}

export default ScenarioPicker
