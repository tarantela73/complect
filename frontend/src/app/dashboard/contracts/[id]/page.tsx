'use client'

import { use } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useCan } from '@/shared/auth/hooks'
import {
  useContract,
  useActivateContract,
  useCloseContract,
  CONTRACT_STATUS_LABELS,
  RESOURCE_TYPE_LABELS,
} from '@/shared/api/contracts'
import { Button } from '@/shared/ui/button'

const STATUS_COLORS: Record<string, string> = {
  DRAFT: 'bg-muted text-muted-foreground',
  ACTIVE: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  LEGAL_HOLD: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  CLOSED: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400',
}

function formatAmount(kopecks: number) {
  return new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB' }).format(kopecks / 100)
}

function Row({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex px-4 py-3 gap-4">
      <dt className="w-44 shrink-0 text-sm font-medium text-muted-foreground">{label}</dt>
      <dd className={`text-sm flex-1 ${mono ? 'font-mono text-xs' : ''}`}>{value}</dd>
    </div>
  )
}

export default function ContractDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const router = useRouter()
  const canWrite = useCan('contracts.write')

  const { data, isLoading, isError, error } = useContract(id)
  const activateMutation = useActivateContract()
  const closeMutation = useCloseContract()

  if (isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Загрузка...</div>
  }

  if (isError) {
    const is404 = error.message === 'NOT_FOUND'
    return (
      <div className="p-6">
        <p className="text-sm text-destructive">
          {is404 ? 'Договор не найден' : 'Ошибка загрузки данных'}
        </p>
        <Button variant="outline" size="sm" className="mt-4" onClick={() => router.back()}>
          ← Назад
        </Button>
      </div>
    )
  }

  if (!data) return null

  const isDraft = data.status === 'DRAFT'
  const isActive = data.status === 'ACTIVE'
  const isMutable = isDraft || isActive

  const handleActivate = () => {
    if (!confirm(`Активировать договор «${data.title}»?`)) return
    activateMutation.mutate(id, {
      onError: (e) => alert(e.message),
    })
  }

  const handleClose = () => {
    if (!confirm(`Закрыть договор «${data.title}»? Это действие необратимо.`)) return
    closeMutation.mutate(id, {
      onError: (e) => alert(e.message),
    })
  }

  return (
    <div className="p-6 max-w-2xl">
      <div className="mb-6 flex items-start gap-4">
        <Link href="/dashboard/contracts" className="text-sm text-muted-foreground hover:text-foreground mt-1">
          ← Договоры
        </Link>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 mb-1">
            <h1 className="text-xl font-semibold truncate">{data.title}</h1>
            <span
              className={`inline-flex shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[data.status] ?? 'bg-muted text-muted-foreground'}`}
            >
              {CONTRACT_STATUS_LABELS[data.status] ?? data.status}
            </span>
          </div>
          <p className="text-xs text-muted-foreground font-mono">{data.contract_number}</p>
        </div>
        {canWrite && isMutable && (
          <div className="flex gap-2 shrink-0">
            {isDraft && (
              <>
                <Link href={`/dashboard/contracts/${id}/edit`}>
                  <Button variant="outline" size="sm">Изменить</Button>
                </Link>
                <Button
                  size="sm"
                  loading={activateMutation.isPending}
                  onClick={handleActivate}
                >
                  Активировать
                </Button>
              </>
            )}
            {isActive && (
              <Button
                variant="destructive"
                size="sm"
                loading={closeMutation.isPending}
                onClick={handleClose}
              >
                Закрыть
              </Button>
            )}
          </div>
        )}
      </div>

      <dl className="divide-y divide-border rounded-md border border-border">
        <Row label="Тип ресурса" value={RESOURCE_TYPE_LABELS[data.resource_type] ?? data.resource_type} />
        <Row label="Сумма договора" value={formatAmount(data.total_amount)} />
        <Row label="Оплачено" value={formatAmount(data.paid_amount)} />
        <Row label="Остаток" value={formatAmount(data.remaining_amount)} />
        <Row
          label="Дата создания"
          value={data.created_at ? new Date(data.created_at).toLocaleString('ru-RU') : '—'}
        />
        <Row label="ID" value={data.id} mono />
      </dl>
    </div>
  )
}
