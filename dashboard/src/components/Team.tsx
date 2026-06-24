import { useState, useEffect, useMemo } from 'react'
import { createApiClient } from '../api'
import Card from './ui/Card'
import Banner from './ui/Banner'
import Button from './ui/Button'
import { actionInfo } from '../labels'

interface ApprovalRule {
  action_type: string
  requires_approval: boolean
}
interface EscalationTarget {
  target: string
  description: string
}
interface TeamSettings {
  team_id: string
  approval_rules: ApprovalRule[]
  escalation_targets: EscalationTarget[]
}

interface Props {
  apiBase: string
}

const TEAM_ID = 'default_team'
const ACTION_TYPES = ['classify', 'reply', 'escalate', 'defer', 'prioritize']

function Team({ apiBase }: Props) {
  const [settings, setSettings] = useState<TeamSettings | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  const client = useMemo(() => createApiClient(apiBase), [apiBase])

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        setSettings(await client.get<TeamSettings>(`/preferences/team/${TEAM_ID}`))
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load team settings')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [client])

  const update = (patch: Partial<TeamSettings>) => setSettings((s) => (s ? { ...s, ...patch } : s))

  const addRule = () =>
    settings &&
    update({
      approval_rules: [
        ...settings.approval_rules,
        { action_type: 'reply', requires_approval: true },
      ],
    })
  const removeRule = (idx: number) =>
    settings && update({ approval_rules: settings.approval_rules.filter((_, i) => i !== idx) })
  const setRule = (idx: number, patch: Partial<ApprovalRule>) =>
    settings &&
    update({
      approval_rules: settings.approval_rules.map((r, i) => (i === idx ? { ...r, ...patch } : r)),
    })

  const addTarget = () =>
    settings &&
    update({
      escalation_targets: [...settings.escalation_targets, { target: '', description: '' }],
    })
  const removeTarget = (idx: number) =>
    settings &&
    update({ escalation_targets: settings.escalation_targets.filter((_, i) => i !== idx) })
  const setTarget = (idx: number, patch: Partial<EscalationTarget>) =>
    settings &&
    update({
      escalation_targets: settings.escalation_targets.map((t, i) =>
        i === idx ? { ...t, ...patch } : t,
      ),
    })

  const save = async () => {
    setSaving(true)
    setError(null)
    setSuccess(false)
    try {
      await client.put(`/preferences/team/${TEAM_ID}`, {
        approval_rules: settings?.approval_rules || [],
        escalation_targets: settings?.escalation_targets || [],
      })
      setSuccess(true)
      setTimeout(() => setSuccess(false), 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save team settings')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <Card>
        <p className="muted">Loading…</p>
      </Card>
    )
  }
  if (!settings) {
    return (
      <Card>
        <p className="muted">No team settings available.</p>
      </Card>
    )
  }

  return (
    <Card>
      <div className="section">
        <h3>What needs your approval</h3>
        <p className="about" style={{ marginBottom: 'var(--space-4)' }}>
          Choose which actions the copilot may take on its own, and which should wait for sign-off.
        </p>
        {settings.approval_rules.map((rule, idx) => (
          <div className="row" key={idx}>
            <select
              className="select"
              value={rule.action_type}
              onChange={(e) => setRule(idx, { action_type: e.target.value })}
              aria-label={`Rule ${idx + 1}: action`}
            >
              {ACTION_TYPES.map((a) => (
                <option key={a} value={a}>
                  {actionInfo(a).label}
                </option>
              ))}
            </select>
            <select
              className="select"
              value={rule.requires_approval ? 'true' : 'false'}
              onChange={(e) => setRule(idx, { requires_approval: e.target.value === 'true' })}
              aria-label={`Rule ${idx + 1}: handling`}
            >
              <option value="true">Wait for approval</option>
              <option value="false">Handle automatically</option>
            </select>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => removeRule(idx)}
              aria-label={`Remove rule ${idx + 1}`}
            >
              Remove
            </Button>
          </div>
        ))}
        <Button variant="secondary" size="sm" onClick={addRule}>
          Add rule
        </Button>
      </div>

      <div className="section">
        <h3>Escalation contacts</h3>
        <p className="about" style={{ marginBottom: 'var(--space-4)' }}>
          People or teams the copilot can hand sensitive email to.
        </p>
        {settings.escalation_targets.map((target, idx) => (
          <div className="row" key={idx}>
            <input
              className="input"
              type="text"
              value={target.target}
              onChange={(e) => setTarget(idx, { target: e.target.value })}
              placeholder="Name or team"
              aria-label={`Contact ${idx + 1}: name`}
            />
            <input
              className="input"
              type="text"
              value={target.description}
              onChange={(e) => setTarget(idx, { description: e.target.value })}
              placeholder="What they handle"
              aria-label={`Contact ${idx + 1}: description`}
            />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => removeTarget(idx)}
              aria-label={`Remove contact ${idx + 1}`}
            >
              Remove
            </Button>
          </div>
        ))}
        <Button variant="secondary" size="sm" onClick={addTarget}>
          Add contact
        </Button>
      </div>

      {error && <Banner kind="error">{error}</Banner>}
      {success && <Banner kind="success">Team settings saved.</Banner>}
      <Button variant="primary" onClick={save} disabled={saving}>
        {saving ? 'Saving…' : 'Save team settings'}
      </Button>
    </Card>
  )
}

export default Team
