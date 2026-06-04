'use client'

import Link from 'next/link'
import { useDashboardSummary } from '@/shared/api/dashboard'
import { useCan } from '@/shared/auth/hooks'

function StatCard({
  label,
  value,
  href,
  sub,
}: {
  label: string
  value: number | string
  href?: string
  sub?: React.ReactNode
}) {
  const content = (
    <div className="rounded-lg border border-border bg-card p-5 hover:border-ring/50 transition-colors">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</p>
      <p className="mt-2 text-3xl font-semibold tabular-nums">{value}</p>
      {sub && <div className="mt-3 space-y-1">{sub}</div>}
    </div>
  )
  return href ? <Link href={href}>{content}</Link> : content
}

function Pill({ label, count, color }: { label: string; count: number; color: string }) {
  return (
    <span className="flex items-center justify-between text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className={`font-medium tabular-nums ${color}`}>{count}</span>
    </span>
  )
}

export default function DashboardPage() {
  const { data, isLoading } = useDashboardSummary()
  const canContracts = useCan('contracts.read')
  const canFiles = useCan('files.read')
  const canContragents = useCan('contragents.read')

  if (isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Загрузка...</div>
  }

  return (
    <div className="p-6">
      <h1 className="mb-6 text-xl font-semibold">Сводка</h1>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {canContracts && data && (
          <StatCard
            label="Договоры"
            value={data.contracts.total}
            href="/dashboard/contracts"
            sub={
              <>
                <Pill label="Активных" count={data.contracts.active} color="text-green-600 dark:text-green-400" />
                <Pill label="Черновиков" count={data.contracts.draft} color="text-muted-foreground" />
                {data.contracts.legal_hold > 0 && (
                  <Pill label="Юр. удержание" count={data.contracts.legal_hold} color="text-yellow-600 dark:text-yellow-400" />
                )}
                <Pill label="Закрытых" count={data.contracts.closed} color="text-muted-foreground" />
              </>
            }
          />
        )}

        {canFiles && data && (
          <StatCard
            label="Файлы"
            value={data.files.total}
            href="/dashboard/files"
            sub={
              <>
                <Pill label="Доступных" count={data.files.available} color="text-green-600 dark:text-green-400" />
                {data.files.scanning > 0 && (
                  <Pill label="Сканируется" count={data.files.scanning} color="text-yellow-600 dark:text-yellow-400" />
                )}
                {data.files.quarantined > 0 && (
                  <Pill label="В карантине" count={data.files.quarantined} color="text-red-600 dark:text-red-400" />
                )}
                {data.files.pending > 0 && (
                  <Pill label="Ожидают обработки" count={data.files.pending} color="text-muted-foreground" />
                )}
              </>
            }
          />
        )}

        {canContragents && data && (
          <StatCard
            label="Контрагенты"
            value={data.contragents_total}
            href="/dashboard/contragents"
          />
        )}
      </div>
    </div>
  )
}
