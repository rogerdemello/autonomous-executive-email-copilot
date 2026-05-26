import { useEffect, useRef, useState } from 'react'

// Live dashboard connection over the /ws/dashboard WebSocket with a ping/pong
// heartbeat and automatic reconnect (capped exponential backoff). Returns the
// connection status and the latest broadcast state.

export interface DashboardSocketState {
  connected: boolean
  lastState: unknown | null
}

function toWsUrl(apiBase: string): string {
  // http(s)://host -> ws(s)://host/ws/dashboard
  const trimmed = apiBase.replace(/\/+$/, '')
  const wsBase = trimmed.replace(/^http/, 'ws')
  return `${wsBase}/ws/dashboard`
}

export function useDashboardSocket(apiBase: string): DashboardSocketState {
  const [connected, setConnected] = useState(false)
  const [lastState, setLastState] = useState<unknown | null>(null)
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    let closedByUs = false
    let attempt = 0
    let ws: WebSocket | null = null

    const cleanupTimers = () => {
      if (pingRef.current) clearInterval(pingRef.current)
      if (reconnectRef.current) clearTimeout(reconnectRef.current)
    }

    const connect = () => {
      try {
        ws = new WebSocket(toWsUrl(apiBase))
      } catch {
        scheduleReconnect()
        return
      }

      ws.onopen = () => {
        attempt = 0
        setConnected(true)
        pingRef.current = setInterval(() => {
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }))
          }
        }, 20000)
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'state_update' || msg.type === 'state') {
            setLastState(msg.data)
          }
        } catch {
          // ignore malformed frames
        }
      }

      ws.onclose = () => {
        setConnected(false)
        if (pingRef.current) clearInterval(pingRef.current)
        if (!closedByUs) scheduleReconnect()
      }

      ws.onerror = () => {
        ws?.close()
      }
    }

    const scheduleReconnect = () => {
      attempt += 1
      const backoff = Math.min(1000 * 2 ** attempt, 15000)
      reconnectRef.current = setTimeout(connect, backoff)
    }

    connect()

    return () => {
      closedByUs = true
      cleanupTimers()
      ws?.close()
    }
  }, [apiBase])

  return { connected, lastState }
}
