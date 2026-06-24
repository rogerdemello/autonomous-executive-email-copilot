import { useState, useEffect, useCallback } from 'react'
import Inbox from './components/Inbox'
import Timeline from './components/Timeline'
import Replay from './components/Replay'
import ApprovalQueue from './components/ApprovalQueue'
import Settings from './components/Settings'
import Team from './components/Team'
import { createApiClient, defaultApiBase } from './api'
import { useDashboardSocket } from './useDashboardSocket'

type Tab = 'inbox' | 'timeline' | 'replay' | 'approvals' | 'settings' | 'team'

const API_BASE = import.meta.env.VITE_API_BASE || defaultApiBase()

interface HealthStatus {
  status: string
}

interface TabDef {
  id: Tab
  label: string
  lede: string
}

// Business-friendly nav. The Inbox label is kept verbatim so the default
// landing screen reads naturally; the rest are reframed from RL/benchmark terms.
const TABS: TabDef[] = [
  { id: 'inbox', label: 'Inbox', lede: 'What the copilot is working on right now' },
  { id: 'timeline', label: 'Activity', lede: 'A step-by-step log of what the copilot did' },
  { id: 'replay', label: 'Replay', lede: 'Step back through a past session' },
  { id: 'approvals', label: 'Approvals', lede: 'Actions waiting for your sign-off' },
  { id: 'team', label: 'Team', lede: 'Who handles escalations, and what needs approval' },
  { id: 'settings', label: 'Preferences', lede: 'Defaults and notifications' },
]

function App() {
  const [activeTab, setActiveTab] = useState<Tab>('inbox')
  const [apiBase, setApiBase] = useState(API_BASE)
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [healthy, setHealthy] = useState(false)

  // Live connection via WebSocket (falls back to the periodic health check).
  const { connected: live } = useDashboardSocket(apiBase)
  const connected = live || healthy

  const checkHealth = useCallback(async () => {
    try {
      const data = await createApiClient(apiBase).get<HealthStatus>('/health', {
        timeoutMs: 5000,
      })
      setHealth(data)
      setHealthy(true)
    } catch {
      setHealthy(false)
    }
  }, [apiBase])

  useEffect(() => {
    checkHealth()
    const interval = setInterval(checkHealth, 30000)
    return () => clearInterval(interval)
  }, [checkHealth])

  const connectionLabel = live ? 'Live' : connected ? 'Connected' : 'Offline'
  const active = TABS.find((t) => t.id === activeTab) ?? TABS[0]

  return (
    <div className="app">
      <header className="sidebar">
        <div className="brand">
          <div className="brand__mark">
            <span className="brand__glyph" aria-hidden="true">
              E
            </span>
            <h1>Email Copilot</h1>
          </div>
          <p className="brand__subtitle">Executive assistant</p>
        </div>
        <nav aria-label="Primary">
          <ul className="nav-list" role="tablist" aria-orientation="vertical">
            {TABS.map((tab) => {
              const selected = activeTab === tab.id
              return (
                <li key={tab.id} role="presentation">
                  <button
                    type="button"
                    role="tab"
                    id={`tab-${tab.id}`}
                    aria-selected={selected}
                    aria-controls={`panel-${tab.id}`}
                    tabIndex={selected ? 0 : -1}
                    className={`nav-item ${selected ? 'active' : ''}`}
                    onClick={() => setActiveTab(tab.id)}
                  >
                    {tab.label}
                  </button>
                </li>
              )
            })}
          </ul>
        </nav>
        <div className="connection-panel">
          <label className="connection-label" htmlFor="api-base">
            Connected service
          </label>
          <input
            id="api-base"
            type="text"
            value={apiBase}
            onChange={(e) => setApiBase(e.target.value)}
            placeholder="Service address"
          />
          <div
            className={`connection-status ${connected ? 'is-connected' : 'is-disconnected'}`}
            role="status"
            aria-live="polite"
          >
            <span className="connection-dot" aria-hidden="true" />
            {connectionLabel}
          </div>
        </div>
      </header>
      <main className="main" id="main-content">
        <div className="main__inner">
          <div className="header">
            <div>
              <h2 className="header__title">{active.label}</h2>
              <p className="header__lede">{active.lede}</p>
            </div>
            {health && <span className="badge badge--ok">Service {health.status}</span>}
          </div>
          <div
            role="tabpanel"
            id={`panel-${activeTab}`}
            aria-labelledby={`tab-${activeTab}`}
            tabIndex={0}
          >
            {activeTab === 'inbox' && <Inbox apiBase={apiBase} />}
            {activeTab === 'timeline' && <Timeline apiBase={apiBase} />}
            {activeTab === 'replay' && <Replay apiBase={apiBase} />}
            {activeTab === 'approvals' && <ApprovalQueue apiBase={apiBase} />}
            {activeTab === 'settings' && <Settings apiBase={apiBase} />}
            {activeTab === 'team' && <Team apiBase={apiBase} />}
          </div>
        </div>
      </main>
    </div>
  )
}

export default App
