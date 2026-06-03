import { useState, useEffect } from 'react'

interface UserPreference {
  user_id: string
  default_persona: string
  notification_email: string | null
}

interface Props {
  apiBase: string
}

function Settings({ apiBase }: Props) {
  const [, setPreference] = useState<UserPreference | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [userId] = useState('default_user')
  const [defaultPersona, setDefaultPersona] = useState('balanced')
  const [notificationEmail, setNotificationEmail] = useState('')

  useEffect(() => {
    loadPreference()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadPreference = async () => {
    setLoading(true)
    try {
      const res = await fetch(`${apiBase}/preferences/user/${userId}`)
      if (res.ok) {
        const data = await res.json()
        setPreference(data)
        setDefaultPersona(data.default_persona || 'balanced')
        setNotificationEmail(data.notification_email || '')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load preferences')
    } finally {
      setLoading(false)
    }
  }

  const savePreference = async () => {
    setSaving(true)
    setError(null)
    setSuccess(false)
    try {
      const res = await fetch(`${apiBase}/preferences/user/${userId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          default_persona: defaultPersona,
          notification_email: notificationEmail || null,
        }),
      })
      if (!res.ok) throw new Error('Failed to save preferences')
      setSuccess(true)
      setTimeout(() => setSuccess(false), 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save preferences')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <div className="card">
        <div className="settings-section">
          <h3>User Preferences</h3>
          {loading ? (
            <p>Loading...</p>
          ) : (
            <>
              <div className="form-group">
                <label htmlFor="settings-user-id">User ID</label>
                <input id="settings-user-id" type="text" value={userId} disabled />
              </div>
              <div className="form-group">
                <label htmlFor="settings-default-persona">Default Persona</label>
                <select
                  id="settings-default-persona"
                  value={defaultPersona}
                  onChange={(e) => setDefaultPersona(e.target.value)}
                >
                  <option value="strict_ceo">strict_ceo</option>
                  <option value="balanced">balanced</option>
                  <option value="chill_manager">chill_manager</option>
                </select>
              </div>
              <div className="form-group">
                <label htmlFor="settings-notification-email">Notification Email</label>
                <input
                  id="settings-notification-email"
                  type="email"
                  value={notificationEmail}
                  onChange={(e) => setNotificationEmail(e.target.value)}
                  placeholder="email@example.com"
                />
              </div>
              {error && (
                <div role="alert" style={{ color: 'var(--danger)', marginBottom: '1rem' }}>
                  {error}
                </div>
              )}
              {success && (
                <div role="status" style={{ color: 'var(--success)', marginBottom: '1rem' }}>
                  Preferences saved!
                </div>
              )}
              <button className="btn btn-primary" onClick={savePreference} disabled={saving}>
                {saving ? 'Saving...' : 'Save Preferences'}
              </button>
            </>
          )}
        </div>

        <div className="settings-section" style={{ marginTop: '2rem' }}>
          <h3>API Settings</h3>
          <div className="form-group">
            <label htmlFor="settings-api-base">API Base URL</label>
            <input id="settings-api-base" type="text" value={apiBase} disabled />
          </div>
          <div className="form-group">
            <label id="settings-docs-label">Documentation</label>
            <a
              href={`${apiBase}/docs`}
              target="_blank"
              rel="noopener noreferrer"
              aria-labelledby="settings-docs-label"
            >
              OpenAPI Docs
            </a>
          </div>
        </div>

        <div className="settings-section" style={{ marginTop: '2rem' }}>
          <h3>About</h3>
          <p style={{ color: 'var(--text-muted)' }}>
            Executive Email Copilot Dashboard v1.0.0
            <br />
            Real-time monitoring and control center for autonomous email management.
          </p>
        </div>
      </div>
    </div>
  )
}

export default Settings
