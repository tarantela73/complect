import { env } from '@/shared/config/env'

type MessageHandler = (data: unknown) => void

interface WsMessage {
  type: string
  payload: unknown
}

export class ErpWebSocketClient {
  private ws: WebSocket | null = null
  private handlers = new Map<string, Set<MessageHandler>>()
  private reconnectAttempts = 0
  private readonly maxReconnects = 5
  private readonly baseDelay = 1000
  private explicitClose = false
  private getToken: () => string | null

  constructor(getToken: () => string | null) {
    this.getToken = getToken
  }

  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) return

    const token = this.getToken()
    if (!token) return

    const url = `${env.NEXT_PUBLIC_WS_URL}/?token=${encodeURIComponent(token)}`
    this.explicitClose = false
    this.ws = new WebSocket(url)

    this.ws.onmessage = (event: MessageEvent<string>) => {
      try {
        const msg = JSON.parse(event.data) as WsMessage
        this.dispatch(msg.type, msg.payload)
      } catch {
        // malformed frame — ignore
      }
    }

    this.ws.onclose = () => {
      if (!this.explicitClose) {
        this.scheduleReconnect()
      }
    }

    this.ws.onerror = () => {
      this.ws?.close()
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0
    }
  }

  disconnect(): void {
    this.explicitClose = true
    this.ws?.close()
    this.ws = null
  }

  on(eventType: string, handler: MessageHandler): () => void {
    if (!this.handlers.has(eventType)) {
      this.handlers.set(eventType, new Set())
    }
    this.handlers.get(eventType)!.add(handler)

    return () => {
      this.handlers.get(eventType)?.delete(handler)
    }
  }

  private dispatch(type: string, payload: unknown): void {
    this.handlers.get(type)?.forEach((h) => h(payload))
    this.handlers.get('*')?.forEach((h) => h({ type, payload }))
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnects) return

    const delay = this.baseDelay * Math.pow(2, this.reconnectAttempts)
    this.reconnectAttempts++

    setTimeout(() => this.connect(), delay)
  }
}

let _client: ErpWebSocketClient | null = null

export function getWsClient(): ErpWebSocketClient {
  if (!_client) {
    const { useAuthStore } = require('@/shared/auth/store') as typeof import('@/shared/auth/store')
    _client = new ErpWebSocketClient(() => useAuthStore.getState().accessToken)
  }
  return _client
}
