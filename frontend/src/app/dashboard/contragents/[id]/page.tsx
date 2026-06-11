'use client'

import { use, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useCan } from '@/shared/auth/hooks'
import { useAuthStore } from '@/shared/auth/store'
import { downloadPdf } from '@/shared/api/reports'
import { useContragent, useDeleteContragent, CONTRAGENT_TYPE_LABELS } from '@/shared/api/contragents'
import { useContracts, CONTRACT_STATUS_LABELS, type Contract } from '@/shared/api/contracts'
import { Button } from '@/shared/ui/button'
import { cn } from '@/shared/ui/utils'

type Tab = 'overview' | 'contracts'

function ContractRow({ contract }: { contract: Contract }) {
  return (
    <tr className="border-t border-border hover:bg-muted/30">
      <td className="px-4 py-3 font-mono text-xs">{contract.contract_number}</td>
      <td className="px-4 py-3 text-sm">{contract.title}</td>
      <td className="px-4 py-3">
        <span className="rounded-full bg-muted px-2 py-0.5 text-xs">
          {CONTRACT_STATUS_LABELS[contract.status] ?? contract.status}
        </span>
      </td>
      <td className="px-4 py-3">
        <Link
          href={`/dashboard/contracts/${contract.id}`}
          className="text-xs text-primary hover:underline"
        >
          Открыть →
        </Link>
      </td>
    </tr>
  )
}

function ContractsTab({ contragentId }: { contragentId: string }) {
  const { data, isLoading } = useContracts({ contragent_id: contragentId, limit: 50 })
  const contracts = data?.results ?? []

  if (isLoading) {
    return <div className="py-8 text-center text-sm text-muted-foreground">Загрузка...</div>
  }

  if (contracts.length === 0) {
    return <div className="py-8 text-center text-sm text-muted-foreground">Договоров нет</div>
  }

  return (
    <div className="rounded-md border border-border overflow-x-auto mt-4">
      <table className="w-full text-sm">
        <thead className="bg-muted/50">
          <tr>
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Номер</th>
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Наименование</th>
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Статус</th>
            <th className="px-4 py-2.5" />
          </tr>
        </thead>
        <tbody>
          {contracts.map((c) => <ContractRow key={c.id} contract={c} />)}
        </tbody>
      </table>
    </div>
  )
}

export default function ContragentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const router = useRouter()
  const canEdit = useCan('contragents.write')
  const canDelete = useCan('contragents.delete')
  const [activeTab, setActiveTab] = useState<Tab>('overview')
  const accessToken = useAuthStore((s) => s.accessToken)
  const [pdfLoading, setPdfLoading] = useState(false)

  const { data, isLoading, isError, error } = useContragent(id)
  const deleteMutation = useDeleteContragent()

  if (isLoading) {
    return (
      <div className="p-6">
        <div className="text-sm text-muted-foreground">Загрузка...</div>
      </div>
    )
  }

  if (isError) {
    const is404 = error.message === 'NOT_FOUND'
    return (
      <div className="p-6">
        <p className="text-sm text-destructive">
          {is404 ? 'Контрагент не найден' : 'Ошибка загрузки данных'}
        </p>
        <Button variant="outline" size="sm" className="mt-4" onClick={() => router.back()}>
          ← Назад
        </Button>
      </div>
    )
  }

  if (!data) return null

  const handleDelete = () => {
    if (!confirm(`Удалить «${data.name}»?`)) return
    deleteMutation.mutate(id, {
      onSuccess: () => router.replace('/dashboard/contragents'),
      onError: (e) => alert(e.message),
    })
  }

  const handlePdf = async () => {
    setPdfLoading(true)
    try {
      await downloadPdf(`/api/v1/reports/contragents/${id}/pdf/`, `contragent_${id}.pdf`, accessToken)
    } catch {
      alert('Ошибка при формировании PDF')
    } finally {
      setPdfLoading(false)
    }
  }

  return (
    <div className="p-6 max-w-2xl">
      <div className="mb-6 flex items-center gap-4">
        <Link
          href="/dashboard/contragents"
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Контрагенты
        </Link>
        <h1 className="text-xl font-semibold flex-1 truncate">{data.name}</h1>
        <div className="flex gap-2 shrink-0">
          <Button variant="outline" size="sm" loading={pdfLoading} onClick={handlePdf}>
            PDF
          </Button>
          {canEdit && (
            <Link href={`/dashboard/contragents/${id}/edit`}>
              <Button variant="outline" size="sm">Изменить</Button>
            </Link>
          )}
          {canDelete && (
            <Button
              variant="destructive"
              size="sm"
              loading={deleteMutation.isPending}
              onClick={handleDelete}
            >
              Удалить
            </Button>
          )}
        </div>
      </div>

      <div className="flex gap-1 border-b border-border mb-4">
        {(['overview', 'contracts'] as Tab[]).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={cn(
              'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
              activeTab === tab
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            )}
          >
            {tab === 'overview' ? 'Обзор' : 'Договоры'}
          </button>
        ))}
      </div>

      {activeTab === 'overview' && (
        <dl className="divide-y divide-border rounded-md border border-border">
          <Row label="ИНН" value={data.inn} />
          <Row
            label="Тип"
            value={CONTRAGENT_TYPE_LABELS[data.contragent_type] ?? data.contragent_type}
          />
          <Row
            label="Дата создания"
            value={data.created_at ? new Date(data.created_at).toLocaleString('ru-RU') : '—'}
          />
          <Row label="ID" value={data.id} mono />
        </dl>
      )}

      {activeTab === 'contracts' && <ContractsTab contragentId={id} />}
    </div>
  )
}

function Row({
  label,
  value,
  mono = false,
}: {
  label: string
  value: string
  mono?: boolean
}) {
  return (
    <div className="flex px-4 py-3 gap-4">
      <dt className="w-40 shrink-0 text-sm font-medium text-muted-foreground">{label}</dt>
      <dd className={`text-sm flex-1 ${mono ? 'font-mono text-xs' : ''}`}>{value}</dd>
    </div>
  )
}
