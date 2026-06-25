'use client'

import Link from 'next/link'
import { useDirectorDashboard } from '@/shared/api/dashboard'

function KpiCard({
  label,
  value,
  sub,
  accent,
}: {
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
  accent?: 'red' | 'green' | 'yellow'
}) {
  const accentClass =
    accent === 'red'
      ? 'border-l-4 border-l-red-500'
      : accent === 'green'
        ? 'border-l-4 border-l-green-500'
        : accent === 'yellow'
          ? 'border-l-4 border-l-yellow-500'
          : ''

  return (
    <div
      className={`rounded-lg border border-border bg-card p-5 ${accentClass}`}
    >
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
        {label}
      </p>
      <p className="mt-2 text-3xl font-semibold tabular-nums">{value}</p>
      {sub && <div className="mt-3 space-y-1">{sub}</div>}
    </div>
  )
}

function MoneyValue({ value }: { value: string }) {
  const num = parseFloat(value)
  const formatted = new Intl.NumberFormat('ru-RU', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(num)
  return <>{formatted} ₽</>
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mb-3 text-sm font-semibold text-muted-foreground uppercase tracking-wide">
      {children}
    </h2>
  )
}

export default function DirectorDashboardPage() {
  const { data, isLoading, isError } = useDirectorDashboard()

  if (isLoading) {
    return (
      <div className="p-6 text-sm text-muted-foreground">Загрузка дашборда...</div>
    )
  }

  if (isError || !data) {
    return (
      <div className="p-6 text-sm text-destructive">
        Ошибка загрузки данных. Попробуйте обновить страницу.
      </div>
    )
  }

  const periodLabel = `${data.period_from} — ${data.period_to}`

  return (
    <div className="p-6 space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Дашборд руководителя</h1>
        <p className="text-sm text-muted-foreground">Период: {periodLabel}</p>
      </div>

      {/* Объекты */}
      <section>
        <SectionHeader>Объекты в работе</SectionHeader>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <KpiCard
            label="В работе"
            value={data.objects_in_progress_count}
            accent="green"
          />
          <KpiCard
            label="Просроченных"
            value={data.overdue_objects_count}
            accent={data.overdue_objects_count > 0 ? 'red' : undefined}
          />
          {data.objects_top5.length > 0 && (
            <KpiCard
              label="Топ объекты"
              value={data.objects_top5.length}
              sub={
                <ul className="space-y-1">
                  {data.objects_top5.map((obj) => (
                    <li key={obj.id} className="text-xs text-muted-foreground">
                      <Link
                        href={`/dashboard/objects/${obj.id}`}
                        className="hover:text-foreground transition-colors truncate block"
                      >
                        {obj.name}
                      </Link>
                    </li>
                  ))}
                </ul>
              }
            />
          )}
        </div>
      </section>

      {/* Договоры */}
      <section>
        <SectionHeader>Договоры</SectionHeader>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <KpiCard
            label="Открытых договоров"
            value={data.open_contracts_count}
            accent={data.open_contracts_count > 0 ? 'yellow' : undefined}
          />
        </div>
      </section>

      {/* Финансы */}
      <section>
        <SectionHeader>Финансы за {data.period_from.slice(0, 7)}</SectionHeader>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <KpiCard
            label="Поступления"
            value={<MoneyValue value={data.payments_credit_month} />}
            accent="green"
          />
          <KpiCard
            label="Расходы"
            value={<MoneyValue value={data.payments_debit_month} />}
            accent="red"
          />
          <KpiCard
            label="Баланс"
            value={
              <MoneyValue
                value={String(
                  parseFloat(data.payments_credit_month) -
                    parseFloat(data.payments_debit_month),
                )}
              />
            }
            accent={
              parseFloat(data.payments_credit_month) >=
              parseFloat(data.payments_debit_month)
                ? 'green'
                : 'red'
            }
          />
        </div>
      </section>
    </div>
  )
}
