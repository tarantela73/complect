'use client'

import { useOpsSnapshot, type OpsSnapshot, type SLOMetricStatus } from '@/shared/api/ops'

function statusColor(value: number, warn: number, crit: number): string {
  if (value >= crit) return 'text-destructive'
  if (value >= warn) return 'text-yellow-600 dark:text-yellow-400'
  return 'text-green-600 dark:text-green-400'
}

function Dot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block h-2.5 w-2.5 rounded-full ${ok ? 'bg-green-500' : 'bg-destructive'}`}
    />
  )
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

function OutboxCard({ data }: { data: OpsSnapshot }) {
  const { pending, failed_permanently, lag_seconds_p95 } = data.outbox
  return (
    <Card title="Outbox">
      <dl className="space-y-2 text-sm">
        <div className="flex justify-between">
          <dt className="text-muted-foreground">Pending</dt>
          <dd className={`font-mono font-medium ${statusColor(pending, 10, 100)}`}>{pending}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-muted-foreground">Permanently failed</dt>
          <dd className={`font-mono font-medium ${statusColor(failed_permanently, 1, 5)}`}>
            {failed_permanently}
          </dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-muted-foreground">Lag p95</dt>
          <dd className={`font-mono font-medium ${lag_seconds_p95 != null ? statusColor(lag_seconds_p95, 30, 300) : ''}`}>
            {lag_seconds_p95 != null ? `${lag_seconds_p95}s` : '—'}
          </dd>
        </div>
      </dl>
    </Card>
  )
}

function CeleryCard({ data }: { data: OpsSnapshot }) {
  const queues = data.celery
  return (
    <Card title="Celery Queues">
      <dl className="space-y-2 text-sm">
        {Object.entries(queues).map(([name, depth]) => (
          <div key={name} className="flex justify-between">
            <dt className="font-mono text-muted-foreground">{name}</dt>
            <dd className={`font-mono font-medium ${statusColor(depth, 50, 500)}`}>
              {depth < 0 ? 'n/a' : depth}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  )
}

function CircuitBreakersCard({ data }: { data: OpsSnapshot }) {
  const breakers = data.circuit_breakers
  return (
    <Card title="Circuit Breakers">
      <dl className="space-y-2 text-sm">
        {Object.entries(breakers).map(([name, state]) => (
          <div key={name} className="flex items-center justify-between">
            <dt className="font-mono text-muted-foreground">{name}</dt>
            <dd className="flex items-center gap-2">
              <Dot ok={state === 'CLOSED'} />
              <span className={`font-mono font-medium ${state === 'OPEN' ? 'text-destructive' : 'text-green-600 dark:text-green-400'}`}>
                {state}
              </span>
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  )
}

const SLO_STATE_STYLES: Record<string, string> = {
  ok: 'text-green-600 dark:text-green-400',
  warn: 'text-yellow-600 dark:text-yellow-400',
  crit: 'text-destructive',
  recovering: 'text-blue-600 dark:text-blue-400',
}

const SLO_DOT_STYLES: Record<string, string> = {
  ok: 'bg-green-500',
  warn: 'bg-yellow-500',
  crit: 'bg-destructive',
  recovering: 'bg-blue-500',
}

function SLOCard({ slo }: { slo: SLOMetricStatus[] }) {
  return (
    <Card title="SLO Status">
      <dl className="space-y-2 text-sm">
        {slo.map((item) => (
          <div key={item.name} className="flex items-center justify-between gap-2">
            <dt className="font-mono text-xs text-muted-foreground leading-tight">
              {item.label}
            </dt>
            <dd className="flex shrink-0 items-center gap-1.5">
              <span
                className={`inline-block h-2 w-2 rounded-full ${SLO_DOT_STYLES[item.state] ?? 'bg-muted'}`}
              />
              <span className={`font-mono font-medium text-xs ${SLO_STATE_STYLES[item.state] ?? ''}`}>
                {item.state}
              </span>
              <span className="ml-1 font-mono text-xs text-muted-foreground">
                {Math.round(item.error_budget * 100)}%
              </span>
            </dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-[10px] text-muted-foreground">error budget = % compliant / 30m</p>
    </Card>
  )
}

function InfraCard({ data }: { data: OpsSnapshot }) {
  return (
    <Card title="Infrastructure">
      <dl className="space-y-2 text-sm">
        <div className="flex justify-between">
          <dt className="text-muted-foreground">DLQ depth</dt>
          <dd className={`font-mono font-medium ${statusColor(data.dlq, 1, 10)}`}>
            {data.dlq < 0 ? 'n/a' : data.dlq}
          </dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-muted-foreground">WS connections</dt>
          <dd className="font-mono font-medium">{data.ws_connections}</dd>
        </div>
      </dl>
    </Card>
  )
}

export default function OpsPage() {
  const { data, isLoading, isError, dataUpdatedAt } = useOpsSnapshot()

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Операционная консоль</h1>
        {dataUpdatedAt > 0 && (
          <span className="text-xs text-muted-foreground">
            Обновлено: {new Date(dataUpdatedAt).toLocaleTimeString('ru-RU')}
          </span>
        )}
      </div>

      {isError && (
        <p className="text-sm text-destructive">Ошибка загрузки метрик</p>
      )}

      {isLoading && (
        <p className="text-sm text-muted-foreground">Загрузка...</p>
      )}

      {data && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <OutboxCard data={data} />
          <CeleryCard data={data} />
          <CircuitBreakersCard data={data} />
          <InfraCard data={data} />
          <SLOCard slo={data.slo} />
        </div>
      )}

      {data && (
        <p className="mt-4 text-xs text-muted-foreground">
          Snapshot at: {data.collected_at} · auto-refresh 30s
        </p>
      )}
    </div>
  )
}
