import { useState, useEffect, useMemo } from 'react'
import { createApiClient } from '../api'
import Card from './ui/Card'
import Banner from './ui/Banner'
import Button from './ui/Button'
import Field from './ui/Field'
import { MANAGEMENT_STYLES } from '../labels'

interface UserPreference {
  user_id: string
  default_persona: string
  notification_email: string | null
}

interface Props {
  apiBase: string
}

const USER_ID = 'default_user'

function Settings({ apiBase }: Props) {
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [defaultPersona, setDefaultPersona] = useState('balanced')
  const [notificationEmail, setNotificationEmail] = useState('')

  const client = useMemo(() => createApiClient(apiBase), [apiBase])

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        const data = await client.get<UserPreference>(`/preferences/user/${USER_ID}`)
        setDefaultPersona(data.default_persona || 'balanced')
        setNotificationEmail(data.notification_email || '')
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load preferences')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [client])

  const save = async () => {
    setSaving(true)
    setError(null)
    setSuccess(false)
    try {
      await client.put(`/preferences/user/${USER_ID}`, {
        default_persona: defaultPersona,
        notification_email: notificationEmail || null,
      })
      setSuccess(true)
      setTimeout(() => setSuccess(false), 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save preferences')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <div className="section">
        <h3>Your preferences</h3>
        {loading ? (
          <p className="muted">Loading…</p>
        ) : (
          <div className="stack">
            <Field
              label="Default management style"
              hint="How firmly the copilot handles your inbox by default"
            >
              {(id) => (
                <select
                  id={id}
                  className="select"
                  value={defaultPersona}
                  onChange={(e) => setDefaultPersona(e.target.value)}
                >
                  {MANAGEMENT_STYLES.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field label="Notification email" hint="Where summaries and alerts are sent">
              {(id) => (
                <input
                  id={id}
                  className="input"
                  type="email"
                  value={notificationEmail}
                  onChange={(e) => setNotificationEmail(e.target.value)}
                  placeholder="you@company.com"
                />
              )}
            </Field>
            {error && <Banner kind="error">{error}</Banner>}
            {success && <Banner kind="success">Preferences saved.</Banner>}
            <div>
              <Button variant="primary" onClick={save} disabled={saving}>
                {saving ? 'Saving…' : 'Save preferences'}
              </Button>
            </div>
          </div>
        )}
      </div>

      <div className="section">
        <h3>About</h3>
        <p className="about">
          Executive Email Copilot — an assistant that triages, drafts, and escalates email on your
          behalf, with your approval on anything sensitive.
        </p>
      </div>
    </Card>
  )
}

export default Settings
