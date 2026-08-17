import { useEffect, useRef } from 'react'

export function useWebSocket(url: string, onMessage: (data: any) => void) {
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}${url}`
    
    const connect = () => {
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          onMessage(data)
        } catch (err) {
          console.error('WebSocket parse error:', err)
        }
      }

      ws.onclose = () => {
        setTimeout(connect, 5000)
      }

      ws.onerror = (err) => {
        console.error('WebSocket error:', err)
        ws.close()
      }
    }

    connect()

    return () => {
      if (wsRef.current) {
        wsRef.current.close()
      }
    }
  }, [url, onMessage])

  return wsRef
}
