import { useState, useEffect, useCallback } from 'react'
import Inbox from './components/Inbox'
import Timeline from './components/Timeline'
import Replay from './components/Replay'
import ApprovalQueue from './components/ApprovalQueue'
import Settings from './components/Settings'
import Team from './components/Team'
import { createApiClient } from './api'
import { useDashboardSocket } from './useDashboardSocket'

type Tab = 'inbox' | 'timeline' | 'replay' | 'approvals' | 'settings' | 'team'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000'

interface HealthStatus {
  status: string
}

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

  const tabs: { id: Tab; label: string }[] = [
    { id: 'inbox', label: 'Inbox' },
    { id: 'timeline', label: 'Timeline' },
    { id: 'replay', label: 'Replay' },
    { id: 'approvals', label: 'Approvals' },
    { id: 'team', label: 'Team' },
    { id: 'settings', label: 'Settings' },
  ]

  const connectionLabel = live ? 'Live' : connected ? 'Connected' : 'Disconnected'
  const activeLabel = tabs.find((t) => t.id === activeTab)?.label || 'Dashboard'

  return (
    <div className="app">
      <header className="sidebar">
        <h1>Email Copilot</h1>
        <p className="subtitle">Dashboard</p>
        <nav aria-label="Primary">
          <ul className="nav-list" role="tablist" aria-orientation="vertical">
            {tabs.map((tab) => {
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
            API Base URL
          </label>
          <input
            id="api-base"
            type="text"
            value={apiBase}
            onChange={(e) => setApiBase(e.target.value)}
            placeholder="API Base URL"
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
        <div className="header">
          <h2>{activeLabel}</h2>
          {health && (
            <span className="status-badge status-success">API: {health.status}</span>
          )}
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
      </main>
    </div>
  )
}

export default App
