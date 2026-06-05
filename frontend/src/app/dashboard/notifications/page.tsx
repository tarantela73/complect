'use client'

import { useState } from 'react'
import {
  useNotifications,
  useMarkRead,
  useMarkAllRead,
  type Notification,
} from '@/shared/api/notifications'
import { Button } from '@/shared/ui/button'

const EVENT_LABELS: Record<string, string> = {
  'contracts.ContractActivated': 'Договор активирован',
  'contracts.ContractClosed': 'Договор закрыт',
  'files.FileQuarantined': 'Файл в карантине',
  'finance.TransactionPosted': 'Транзакция',
}

const EVENT_COLORS: Record<string, string> = {
  'contracts.ContractActivated': 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  'contracts.ContractClosed': 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400',
  'files.FileQuarantined': 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
  'finance.TransactionPosted': 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
}

function formatDate(iso: string) {
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Moscow',
  }).format(new Date(iso))
}

function NotificationRow({ notif }: { notif: Notification }) {
  const markRead = useMarkRead()

  return (
    <div
      className={`flex gap-3 border-b border-border p-4 transition-colors hover:bg-muted/20 ${
        notif.is_read ? 'opacity-60' : ''
      }`}
    >
      <div className="mt-0.5 flex-shrink-0">
        <span
          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
            EVENT_COLORS[notif.event_type] ?? 'bg-muted text-muted-foreground'
          }`}
        >
          {EVENT_LABELS[notif.event_type] ?? notif.event_type}
        </span>
      </div>

      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">{notif.title}</p>
        <p className="mt-0.5 text-sm text-muted-foreground">{notif.body}</p>
        <p className="mt-1 text-xs text-muted-foreground/60">{formatDate(notif.created_at)}</p>
      </div>

      {!notif.is_read && (
        <div className="flex flex-shrink-0 items-start gap-2">
          <div className="mt-1.5 h-2 w-2 rounded-full bg-blue-500" title="Непрочитано" />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => markRead.mutate(notif.id)}
            disabled={markRead.isPending}
            className="text-xs"
          >
            Прочитать
          </Button>
        </div>
      )}
    </div>
  )
}

export default function NotificationsPage() {
  const [cursors, setCursors] = useState<string[]>([])
  const currentCursor = cursors.at(-1)
  const markAllRead = useMarkAllRead()

  const { data, isLoading, isError } = useNotifications({
    cursor: currentCursor,
    limit: 20,
  })

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Уведомления</h1>
          {data && (
            <p className="mt-0.5 text-sm text-muted-foreground">Всего: {data.count}</p>
          )}
        </div>
        {data && data.results.some((n) => !n.is_read) && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => markAllRead.mutate()}
            disabled={markAllRead.isPending}
          >
            Прочитать все
          </Button>
        )}
      </div>

      {isError && (
        <p className="text-sm text-destructive">Ошибка загрузки уведомлений</p>
      )}

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Загрузка...</p>
      ) : (
        <>
          <div className="rounded-md border border-border overflow-hidden">
            {!data?.results.length ? (
              <div className="py-12 text-center text-sm text-muted-foreground">
                Уведомлений нет
              </div>
            ) : (
              data.results.map((n) => <NotificationRow key={n.id} notif={n} />)
            )}
          </div>

          <div className="mt-4 flex gap-2">
            {cursors.length > 0 && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setCursors((prev) => prev.slice(0, -1))}
              >
                ← Назад
              </Button>
            )}
            {data?.next_cursor && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setCursors((prev) => [...prev, data.next_cursor!])}
              >
                Вперёд →
              </Button>
            )}
          </div>
        </>
      )}
    </div>
  )
}
