import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'
import { env } from '@/shared/config/env'
import { useAuthStore } from '@/shared/auth/store'

export type Notification = components['schemas']['NotificationSchema']
export type NotificationList = components['schemas']['NotificationListResponse']
export type UnreadCountResponse = components['schemas']['UnreadCountResponse']

export const notificationKeys = {
  all: ['notifications'] as const,
  lists: () => [...notificationKeys.all, 'list'] as const,
  list: (params: Record<string, unknown>) => [...notificationKeys.lists(), params] as const,
  unreadCount: () => [...notificationKeys.all, 'unread-count'] as const,
}

export function useNotifications(params?: { cursor?: string; limit?: number }) {
  return useQuery({
    queryKey: notificationKeys.list(params ?? {}),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/notifications/', {
        params: { query: params },
      })
      if (error) throw new Error('Ошибка загрузки уведомлений')
      return data as NotificationList
    },
  })
}

export function useUnreadCount() {
  return useQuery({
    queryKey: notificationKeys.unreadCount(),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/notifications/unread-count/', {})
      if (error) throw new Error('Ошибка загрузки счётчика')
      return data as UnreadCountResponse
    },
    refetchInterval: 30_000,
  })
}

export function useMarkRead() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (notifId: string) => {
      const { error } = await apiClient.POST('/api/v1/notifications/{notif_id}/read/', {
        params: { path: { notif_id: notifId } },
      })
      if (error) throw new Error('Ошибка')
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: notificationKeys.lists() })
      qc.invalidateQueries({ queryKey: notificationKeys.unreadCount() })
    },
  })
}

export function useMarkAllRead() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const { error } = await apiClient.POST('/api/v1/notifications/read-all/', {})
      if (error) throw new Error('Ошибка')
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: notificationKeys.lists() })
      qc.invalidateQueries({ queryKey: notificationKeys.unreadCount() })
    },
  })
}

const WS_RECONNECT_DELAY_MS = 5_000

export function useNotificationSocket() {
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
        let msg: { type: string; event_type?: string; payload?: { user_id?: string } }
        try {
          msg = JSON.parse(e.data)
        } catch {
          return
        }

        if (msg.type === 'realtime_event' && msg.event_type === 'notification.new') {
          qc.invalidateQueries({ queryKey: notificationKeys.unreadCount() })
        }
      }

      ws.onclose = () => {
        if (activeRef.current) {
          reconnectRef.current = setTimeout(connect, WS_RECONNECT_DELAY_MS)
        }
      }

      ws.onerror = () => {
        // onclose fires after onerror
      }
    }

    connect()

    return () => {
      activeRef.current = false
      if (reconnectRef.current) clearTimeout(reconnectRef.current)
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [accessToken, tenantId, qc])
}
