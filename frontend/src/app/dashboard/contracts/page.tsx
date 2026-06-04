'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useCan } from '@/shared/auth/hooks'
import {
  useContracts,
  CONTRACT_STATUS_LABELS,
  RESOURCE_TYPE_LABELS,
  CONTRACT_STATUSES,
  RESOURCE_TYPES,
  type Contract,
} from '@/shared/api/contracts'
import { Button } from '@/shared/ui/button'

const STATUS_COLORS: Record<string, string> = {
  DRAFT: 'bg-muted text-muted-foreground',
  ACTIVE: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  LEGAL_HOLD: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  CLOSED: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400',
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[status] ?? 'bg-muted text-muted-foreground'}`}>
      {CONTRACT_STATUS_LABELS[status] ?? status}
    </span>
  )
}

function formatAmount(kopecks: number) {
  return new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB' }).format(kopecks / 100)
}

function ContractRow({ contract }: { contract: Contract }) {
  return (
    <tr className="border-b border-border hover:bg-muted/30 transition-colors">
      <td className="px-4 py-3 text-xs font-mono">{contract.contract_number}</td>
      <td className="px-4 py-3 text-sm">
        <Link
          href={`/dashboard/contracts/${contract.id}`}
          className="font-medium hover:underline text-foreground"
        >
          {contract.title}
        </Link>
      </td>
      <td className="px-4 py-3 text-sm">
        {RESOURCE_TYPE_LABELS[contract.resource_type] ?? contract.resource_type}
      </td>
      <td className="px-4 py-3">
        <StatusBadge status={contract.status} />
      </td>
      <td className="px-4 py-3 text-sm text-right font-mono">
        {formatAmount(contract.total_amount)}
      </td>
      <td className="px-4 py-3 text-sm text-right font-mono text-muted-foreground">
        {formatAmount(contract.remaining_amount)}
      </td>
      <td className="px-4 py-3 text-sm text-right">
        <Link href={`/dashboard/contracts/${contract.id}`}>
          <Button variant="ghost" size="sm">→</Button>
        </Link>
      </td>
    </tr>
  )
}

export default function ContractsPage() {
  const canCreate = useCan('contracts.create')

  const [statusFilter, setStatusFilter] = useState('')
  const [resourceFilter, setResourceFilter] = useState('')
  const [cursors, setCursors] = useState<string[]>([])
  const currentCursor = cursors.at(-1)

  const { data, isLoading, isError } = useContracts({
    cursor: currentCursor,
    limit: 20,
    status: statusFilter || undefined,
    resource_type: resourceFilter || undefined,
  })

  const handleFilterChange = () => {
    setCursors([])
  }

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Договоры</h1>
        {canCreate && (
          <Link href="/dashboard/contracts/create">
            <Button size="sm">Создать договор</Button>
          </Link>
        )}
      </div>

      <div className="mb-4 flex gap-3">
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); handleFilterChange() }}
          className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <option value="">Все статусы</option>
          {CONTRACT_STATUSES.map((s) => (
            <option key={s} value={s}>{CONTRACT_STATUS_LABELS[s]}</option>
          ))}
        </select>

        <select
          value={resourceFilter}
          onChange={(e) => { setResourceFilter(e.target.value); handleFilterChange() }}
          className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <option value="">Все типы ресурсов</option>
          {RESOURCE_TYPES.map((r) => (
            <option key={r} value={r}>{RESOURCE_TYPE_LABELS[r]}</option>
          ))}
        </select>
      </div>

      {isError && (
        <p className="text-sm text-destructive">Ошибка загрузки договоров</p>
      )}

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Загрузка...</p>
      ) : (
        <>
          <div className="rounded-md border border-border overflow-hidden">
            <table className="w-full">
              <thead className="bg-muted/50">
                <tr>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">№ договора</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Наименование</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Тип ресурса</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Статус</th>
                  <th className="px-4 py-2.5 text-right text-xs font-medium text-muted-foreground">Сумма</th>
                  <th className="px-4 py-2.5 text-right text-xs font-medium text-muted-foreground">Остаток</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {data?.results.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-sm text-muted-foreground">
                      Договоры не найдены
                    </td>
                  </tr>
                ) : (
                  data?.results.map((c) => (
                    <ContractRow key={c.id} contract={c} />
                  ))
                )}
              </tbody>
            </table>
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
