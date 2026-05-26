import { useState, useEffect } from 'react'

interface TeamSettings {
  team_id: string
  approval_rules: Array<{ action_type: string; requires_approval: boolean }>
  escalation_targets: Array<{ target: string; description: string }>
}

interface Props {
  apiBase: string
}

function Team({ apiBase }: Props) {
  const [teamSettings, setTeamSettings] = useState<TeamSettings | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [teamId] = useState('default_team')

  useEffect(() => {
    loadTeamSettings()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadTeamSettings = async () => {
    setLoading(true)
    try {
      const res = await fetch(`${apiBase}/preferences/team/${teamId}`)
      if (res.ok) {
        const data = await res.json()
        setTeamSettings(data)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load team settings')
    } finally {
      setLoading(false)
    }
  }

  const addApprovalRule = () => {
    if (teamSettings) {
      setTeamSettings({
        ...teamSettings,
        approval_rules: [
          ...teamSettings.approval_rules,
          { action_type: 'reply', requires_approval: false },
        ],
      })
    }
  }

  const removeApprovalRule = (index: number) => {
    if (teamSettings) {
      const newRules = [...teamSettings.approval_rules]
      newRules.splice(index, 1)
      setTeamSettings({ ...teamSettings, approval_rules: newRules })
    }
  }

  const updateApprovalRule = (index: number, field: string, value: string | boolean) => {
    if (teamSettings) {
      const newRules = [...teamSettings.approval_rules]
      newRules[index] = { ...newRules[index], [field]: value }
      setTeamSettings({ ...teamSettings, approval_rules: newRules })
    }
  }

  const addEscalationTarget = () => {
    if (teamSettings) {
      setTeamSettings({
        ...teamSettings,
        escalation_targets: [...teamSettings.escalation_targets, { target: '', description: '' }],
      })
    }
  }

  const removeEscalationTarget = (index: number) => {
    if (teamSettings) {
      const newTargets = [...teamSettings.escalation_targets]
      newTargets.splice(index, 1)
      setTeamSettings({ ...teamSettings, escalation_targets: newTargets })
    }
  }

  const updateEscalationTarget = (index: number, field: string, value: string) => {
    if (teamSettings) {
      const newTargets = [...teamSettings.escalation_targets]
      newTargets[index] = { ...newTargets[index], [field]: value }
      setTeamSettings({ ...teamSettings, escalation_targets: newTargets })
    }
  }

  const saveTeamSettings = async () => {
    setSaving(true)
    setError(null)
    setSuccess(false)
    try {
      const res = await fetch(`${apiBase}/preferences/team/${teamId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          approval_rules: teamSettings?.approval_rules || [],
          escalation_targets: teamSettings?.escalation_targets || [],
        }),
      })
      if (!res.ok) throw new Error('Failed to save team settings')
      setSuccess(true)
      setTimeout(() => setSuccess(false), 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save team settings')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <div className="card">
        <div className="settings-section">
          <h3>Team Settings</h3>
          {loading ? (
            <p>Loading...</p>
          ) : teamSettings ? (
            <>
              <div className="form-group">
                <label>Team ID</label>
                <input type="text" value={teamId} disabled />
              </div>

              <h4 style={{ marginTop: '1.5rem', marginBottom: '1rem' }}>Approval Rules</h4>
              {teamSettings.approval_rules.map((rule, idx) => (
                <div key={idx} style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
                  <select
                    value={rule.action_type}
                    onChange={(e) => updateApprovalRule(idx, 'action_type', e.target.value)}
                  >
                    <option value="classify">classify</option>
                    <option value="reply">reply</option>
                    <option value="escalate">escalate</option>
                    <option value="defer">defer</option>
                    <option value="prioritize">prioritize</option>
                  </select>
                  <select
                    value={rule.requires_approval ? 'true' : 'false'}
                    onChange={(e) =>
                      updateApprovalRule(idx, 'requires_approval', e.target.value === 'true')
                    }
                  >
                    <option value="true">Requires Approval</option>
                    <option value="false">Auto-approve</option>
                  </select>
                  <button className="btn" onClick={() => removeApprovalRule(idx)}>
                    Remove
                  </button>
                </div>
              ))}
              <button className="btn" onClick={addApprovalRule}>
                Add Rule
              </button>

              <h4 style={{ marginTop: '1.5rem', marginBottom: '1rem' }}>Escalation Targets</h4>
              {teamSettings.escalation_targets.map((target, idx) => (
                <div key={idx} style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
                  <input
                    type="text"
                    value={target.target}
                    onChange={(e) => updateEscalationTarget(idx, 'target', e.target.value)}
                    placeholder="Target name"
                    style={{ flex: 1 }}
                  />
                  <input
                    type="text"
                    value={target.description}
                    onChange={(e) => updateEscalationTarget(idx, 'description', e.target.value)}
                    placeholder="Description"
                    style={{ flex: 2 }}
                  />
                  <button className="btn" onClick={() => removeEscalationTarget(idx)}>
                    Remove
                  </button>
                </div>
              ))}
              <button className="btn" onClick={addEscalationTarget}>
                Add Target
              </button>

              {error && <div style={{ color: 'var(--danger)', marginTop: '1rem' }}>{error}</div>}
              {success && (
                <div style={{ color: 'var(--success)', marginTop: '1rem' }}>
                  Team settings saved!
                </div>
              )}
              <button
                className="btn btn-primary"
                onClick={saveTeamSettings}
                disabled={saving}
                style={{ marginTop: '1rem' }}
              >
                {saving ? 'Saving...' : 'Save Team Settings'}
              </button>
            </>
          ) : (
            <p>No team settings loaded</p>
          )}
        </div>
      </div>
    </div>
  )
}

export default Team
