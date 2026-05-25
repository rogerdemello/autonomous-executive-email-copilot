import { useState, useEffect, useCallback } from 'react'
import Inbox from './components/Inbox'
import Timeline from './components/Timeline'
import Replay from './components/Replay'
import ApprovalQueue from './components/ApprovalQueue'
import Settings from './components/Settings'
import Team from './components/Team'

type Tab = 'inbox' | 'timeline' | 'replay' | 'approvals' | 'settings' | 'team'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000'

interface HealthStatus {
  status: string
}

function App() {
  const [activeTab, setActiveTab] = useState<Tab>('inbox')
  const [apiBase, setApiBase] = useState(API_BASE)
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [connected, setConnected] = useState(false)

  const checkHealth = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/health`)
      if (res.ok) {
        const data = await res.json()
        setHealth(data)
        setConnected(true)
      } else {
        setConnected(false)
      }
    } catch {
      setConnected(false)
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

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>Email Copilot</h1>
        <p className="subtitle">Dashboard</p>
        <nav>
          {tabs.map(tab => (
            <div
              key={tab.id}
              className={`nav-item ${activeTab === tab.id ? 'active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </div>
          ))}
        </nav>
        <div className="connection-panel">
          <input
            type="text"
            value={apiBase}
            onChange={e => setApiBase(e.target.value)}
            placeholder="API Base URL"
          />
          <div style={{ fontSize: '0.75rem', color: connected ? '#22c55e' : '#ef4444' }}>
            {connected ? 'Connected' : 'Disconnected'}
          </div>
        </div>
      </aside>
      <main className="main">
        <div className="header">
          <h2>{tabs.find(t => t.id === activeTab)?.label || 'Dashboard'}</h2>
          {health && (
            <span className="status-badge status-success">API: {health.status}</span>
          )}
        </div>
        {activeTab === 'inbox' && <Inbox apiBase={apiBase} />}
        {activeTab === 'timeline' && <Timeline apiBase={apiBase} />}
        {activeTab === 'replay' && <Replay apiBase={apiBase} />}
        {activeTab === 'approvals' && <ApprovalQueue apiBase={apiBase} />}
        {activeTab === 'settings' && <Settings apiBase={apiBase} />}
        {activeTab === 'team' && <Team apiBase={apiBase} />}
      </main>
    </div>
  )
}

export default App