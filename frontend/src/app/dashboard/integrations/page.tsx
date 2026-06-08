'use client'

import { useState } from 'react'
import {
  useWebhookStats,
  useWebhooks,
  useWebhookDeliveries,
  useCreateWebhook,
  useToggleWebhook,
  useDeleteWebhook,
  useReplayDelivery,
  type WebhookSubscription,
  type WebhookDelivery,
} from '@/shared/api/webhooks'

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function statusBadge(status: string) {
  const cls: Record<string, string> = {
    DELIVERED: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
    FAILED: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
    DEAD: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
    PENDING: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  }
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${cls[status] ?? 'bg-muted text-muted-foreground'}`}
    >
      {status}
    </span>
  )
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString('ru-RU', {
    timeZone: 'Europe/Moscow',
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <h2 className="mb-4 text-xs font-semibold uppercase tracking-widest text-muted-foreground">
        {title}
      </h2>
      {children}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------

function StatsCard() {
  const { data, isLoading, error } = useWebhookStats()

  if (isLoading) return <Card title="Health"><p className="text-sm text-muted-foreground">Загрузка...</p></Card>
  if (error || !data) return <Card title="Health"><p className="text-sm text-destructive">Ошибка загрузки</p></Card>

  const successColor =
    data.deliveries_24h === 0 ? ''
    : data.success_rate >= 99 ? 'text-green-600 dark:text-green-400'
    : data.success_rate >= 95 ? 'text-yellow-600 dark:text-yellow-400'
    : 'text-destructive'

  return (
    <Card title="Health">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-6">
        <div>
          <dt className="text-muted-foreground">Активных подписок</dt>
          <dd className="mt-1 font-mono text-xl font-semibold">{data.active_subscriptions}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Доставок за 24ч</dt>
          <dd className="mt-1 font-mono text-xl font-semibold">{data.deliveries_24h}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Успешность</dt>
          <dd className={`mt-1 font-mono text-xl font-semibold ${successColor}`}>
            {data.success_rate}%
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Ожидают retry</dt>
          <dd className={`mt-1 font-mono text-xl font-semibold ${data.failed_count > 0 ? 'text-yellow-600 dark:text-yellow-400' : ''}`}>
            {data.failed_count}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">DEAD</dt>
          <dd className={`mt-1 font-mono text-xl font-semibold ${data.dead_count > 0 ? 'text-destructive' : ''}`}>
            {data.dead_count}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Avg latency</dt>
          <dd className="mt-1 font-mono text-xl font-semibold">
            {data.avg_duration_ms != null ? `${data.avg_duration_ms}ms` : '—'}
          </dd>
        </div>
      </dl>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Create form
// ---------------------------------------------------------------------------

const ALL_EVENT_TYPES = [
  'contracts.ContractCreated',
  'contracts.ContractActivated',
  'contracts.ContractClosed',
  'finance.TransactionPosted',
  'files.FileUploaded',
  'files.FileAvailable',
  'files.FileQuarantined',
  'slo.state_changed',
]

function CreateWebhookForm({ onDone }: { onDone: () => void }) {
  const [url, setUrl] = useState('')
  const [secret, setSecret] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [err, setErr] = useState('')
  const create = useCreateWebhook()

  const toggle = (et: string) =>
    setSelected((s) => (s.includes(et) ? s.filter((x) => x !== et) : [...s, et]))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr('')
    if (!url || !secret || selected.length === 0) {
      setErr('Заполните URL, Secret и выберите хотя бы один тип события.')
      return
    }
    try {
      await create.mutateAsync({ url, secret, event_types: selected })
      onDone()
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Ошибка')
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">URL</label>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/webhook"
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between">
            <label className="text-xs font-medium text-muted-foreground">Secret</label>
            <button
              type="button"
              onClick={() => {
                const bytes = new Uint8Array(32)
                crypto.getRandomValues(bytes)
                setSecret(Array.from(bytes).map((b) => b.toString(16).padStart(2, '0')).join(''))
              }}
              className="text-xs text-primary hover:underline"
            >
              Сгенерировать
            </button>
          </div>
          <input
            type="text"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder="Секрет для HMAC-SHA256"
            className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
          {secret && (
            <p className="mt-1 text-xs text-muted-foreground">
              Сохраните секрет — после создания он не отображается повторно.
            </p>
          )}
        </div>
      </div>
      <div>
        <p className="mb-2 text-xs font-medium text-muted-foreground">Типы событий</p>
        <div className="flex flex-wrap gap-2">
          {ALL_EVENT_TYPES.map((et) => (
            <button
              key={et}
              type="button"
              onClick={() => toggle(et)}
              className={`rounded-full border px-3 py-1 text-xs font-mono transition-colors ${
                selected.includes(et)
                  ? 'border-primary bg-primary text-primary-foreground'
                  : 'border-border bg-background text-muted-foreground hover:border-primary'
              }`}
            >
              {et}
            </button>
          ))}
        </div>
      </div>
      {err && <p className="text-xs text-destructive">{err}</p>}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={create.isPending}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {create.isPending ? 'Создание...' : 'Создать'}
        </button>
        <button
          type="button"
          onClick={onDone}
          className="rounded-md border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-accent"
        >
          Отмена
        </button>
      </div>
    </form>
  )
}

// ---------------------------------------------------------------------------
// Deliveries drawer
// ---------------------------------------------------------------------------

function DeliveriesPanel({ sub }: { sub: WebhookSubscription }) {
  const { data, isLoading } = useWebhookDeliveries(String(sub.id))
  const replay = useReplayDelivery(String(sub.id))

  return (
    <div className="mt-4 rounded-md border border-border bg-muted/30 p-4">
      <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-muted-foreground">
        Лог доставок — {sub.url}
      </p>
      {isLoading && <p className="text-sm text-muted-foreground">Загрузка...</p>}
      {data?.items && data.items.length === 0 && (
        <p className="text-sm text-muted-foreground">Доставок ещё не было.</p>
      )}
      {data?.items && data.items.length > 0 && (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th className="pb-2 pr-4 font-medium">Событие</th>
              <th className="pb-2 pr-4 font-medium">Попытка</th>
              <th className="pb-2 pr-4 font-medium">Статус</th>
              <th className="pb-2 pr-4 font-medium">HTTP</th>
              <th className="pb-2 pr-4 font-medium">Latency</th>
              <th className="pb-2 pr-4 font-medium">Время</th>
              <th className="pb-2 font-medium"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {data.items.map((d: WebhookDelivery) => (
              <tr key={String(d.id)} className="align-top">
                <td className="py-2 pr-4 font-mono">{d.event_type}</td>
                <td className="py-2 pr-4 font-mono">{d.attempt}</td>
                <td className="py-2 pr-4">{statusBadge(d.status)}</td>
                <td className="py-2 pr-4 font-mono">{d.response_code ?? '—'}</td>
                <td className="py-2 pr-4 font-mono">{d.duration_ms != null ? `${d.duration_ms}ms` : '—'}</td>
                <td className="py-2 pr-4 font-mono text-muted-foreground">{fmtDate(d.created_at)}</td>
                <td className="py-2">
                  {d.status === 'DEAD' && (
                    <button
                      onClick={() => replay.mutate(String(d.id))}
                      disabled={replay.isPending}
                      className="rounded border border-orange-400/50 px-2 py-0.5 text-xs font-medium text-orange-600 hover:bg-orange-50 disabled:opacity-50 dark:text-orange-400 dark:hover:bg-orange-950/30"
                    >
                      {replay.isPending ? '...' : 'Replay'}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Subscription row
// ---------------------------------------------------------------------------

function SubscriptionRow({ sub }: { sub: WebhookSubscription }) {
  const [expanded, setExpanded] = useState(false)
  const toggle = useToggleWebhook()
  const del = useDeleteWebhook()

  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="flex items-start justify-between gap-4 p-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className={`h-2 w-2 rounded-full shrink-0 ${sub.is_active ? 'bg-green-500' : 'bg-muted-foreground'}`}
            />
            <span className="truncate font-mono text-sm">{sub.url}</span>
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {sub.event_types.map((et) => (
              <span
                key={et}
                className="rounded-full bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground"
              >
                {et}
              </span>
            ))}
          </div>
          {sub.last_delivery_at && (
            <p className="mt-1 text-xs text-muted-foreground">
              Последняя доставка: {fmtDate(sub.last_delivery_at)}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            onClick={() => setExpanded((v) => !v)}
            className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-accent"
          >
            {expanded ? 'Скрыть' : 'Доставки'}
          </button>
          <button
            onClick={() => toggle.mutate({ id: String(sub.id), is_active: !sub.is_active })}
            disabled={toggle.isPending}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50 ${
              sub.is_active
                ? 'border border-border text-muted-foreground hover:bg-accent'
                : 'bg-primary text-primary-foreground hover:bg-primary/90'
            }`}
          >
            {sub.is_active ? 'Выключить' : 'Включить'}
          </button>
          <button
            onClick={() => {
              if (confirm('Удалить подписку?')) del.mutate(String(sub.id))
            }}
            disabled={del.isPending}
            className="rounded-md border border-destructive/40 px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
          >
            Удалить
          </button>
        </div>
      </div>
      {expanded && <DeliveriesPanel sub={sub} />}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Subscriptions list
// ---------------------------------------------------------------------------

function SubscriptionsCard() {
  const [showForm, setShowForm] = useState(false)
  const { data, isLoading, error } = useWebhooks()

  return (
    <Card title="Подписки">
      {!showForm && (
        <div className="mb-4">
          <button
            onClick={() => setShowForm(true)}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            + Новая подписка
          </button>
        </div>
      )}
      {showForm && (
        <div className="mb-6 rounded-lg border border-border p-4">
          <CreateWebhookForm onDone={() => setShowForm(false)} />
        </div>
      )}
      {isLoading && <p className="text-sm text-muted-foreground">Загрузка подписок...</p>}
      {error && <p className="text-sm text-destructive">Ошибка загрузки подписок.</p>}
      {data?.items && data.items.length === 0 && (
        <p className="text-sm text-muted-foreground">Нет подписок. Создайте первую.</p>
      )}
      <div className="space-y-3">
        {data?.items?.map((sub: WebhookSubscription) => (
          <SubscriptionRow key={String(sub.id)} sub={sub} />
        ))}
      </div>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function IntegrationsPage() {
  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-xl font-semibold">Интеграции</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Webhook-подписки и лог доставок событий внешним системам
        </p>
      </div>
      <div className="space-y-6">
        <StatsCard />
        <SubscriptionsCard />
      </div>
    </div>
  )
}
