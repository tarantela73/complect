import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { env } from '@/shared/config/env'
import { useAuthStore } from '@/shared/auth/store'
import { fileKeys } from './files'

// Module-level flag: true while at least one WS connection is healthy.
// Used by useFile to decide whether to fall back to polling.
let _wsConnected = false
export const isWsConnected = () => _wsConnected

const WS_RECONNECT_DELAY_MS = 5_000
const WS_FALLBACK_POLL_MS = 30_000

type FileStatusPayload = { file_id: string; status: string }

export function useFileStatusSocket() {
  const qc = useQueryClient()
  const accessToken = useAuthStore((s) => s.accessToken)
  const tenantId = useAuthStore((s) => s.tenantId)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const activeRef = useRef(false)

  useEffect(() => {
    if (!accessToken || !tenantId) return

    activeRef.current = true

    const connect = () => {
      if (!activeRef.current) return

      const url = `${env.NEXT_PUBLIC_WS_URL}/realtime/`
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        ws.send(
          JSON.stringify({
            type: 'connection_init',
            protocol_version: 1,
            capabilities: ['at_least_once'],
            token: accessToken,
          }),
        )
      }

      ws.onmessage = (e) => {
        let msg: { type: string; event_type?: string; payload?: FileStatusPayload }
        try {
          msg = JSON.parse(e.data)
        } catch {
          return
        }

        if (msg.type === 'connection_ack') {
          _wsConnected = true
          return
        }

        if (
          msg.type === 'realtime_event' &&
          msg.event_type === 'file.status_changed' &&
          msg.payload
        ) {
          const { file_id, status } = msg.payload
          // Optimistic cache update — avoids a round-trip fetch
          qc.setQueryData(fileKeys.detail(file_id), (old: Record<string, unknown> | undefined) =>
            old ? { ...old, status } : old,
          )
          // Invalidate list so status badge refreshes without extra poll
          qc.invalidateQueries({ queryKey: fileKeys.lists() })
        }
      }

      ws.onclose = () => {
        _wsConnected = false
        if (activeRef.current) {
          reconnectRef.current = setTimeout(connect, WS_RECONNECT_DELAY_MS)
        }
      }

      ws.onerror = () => {
        // onclose fires after onerror — reconnect handled there
      }
    }

    connect()

    return () => {
      activeRef.current = false
      _wsConnected = false
      if (reconnectRef.current) clearTimeout(reconnectRef.current)
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [accessToken, tenantId, qc])
}

export { WS_FALLBACK_POLL_MS }
