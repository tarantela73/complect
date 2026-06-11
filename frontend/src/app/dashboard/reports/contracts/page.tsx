'use client'

import { useState } from 'react'
import { useContracts, CONTRACT_STATUS_LABELS, RESOURCE_TYPE_LABELS } from '@/shared/api/contracts'
import { downloadReport } from '@/shared/api/reports'
import { Button } from '@/shared/ui/button'

const STATUS_OPTIONS = [
  { value: '', label: 'Все статусы' },
  ...Object.entries(CONTRACT_STATUS_LABELS).map(([v, l]) => ({ value: v, label: l })),
]

const RESOURCE_OPTIONS = [
  { value: '', label: 'Все ресурсы' },
  ...Object.entries(RESOURCE_TYPE_LABELS).map(([v, l]) => ({ value: v, label: l })),
]

function buildParams(filters: Record<string, string>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(filters)) {
    if (v) p.set(k, v)
  }
  return p.toString()
}

export default function ContractsReportPage() {
  const [status, setStatus] = useState('')
  const [resourceType, setResourceType] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [contragentId, setContragentId] = useState('')

  const filters = {
    status,
    resource_type: resourceType,
    date_from: dateFrom,
    date_to: dateTo,
    contragent_id: contragentId,
  }

  const { data, isLoading } = useContracts({
    status: status || undefined,
    resource_type: resourceType || undefined,
    contragent_id: contragentId || undefined,
    limit: 20,
  })

  const contracts = data?.results ?? []

  function handleExport() {
    const qs = buildParams(filters)
    downloadReport(`/api/v1/reports/contracts/export/${qs ? '?' + qs : ''}`, 'contracts_report.xlsx')
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold">Отчёт: Договоры</h1>
          <p className="text-muted-foreground text-sm mt-0.5">Превью — первые 20 записей</p>
        </div>
        <Button onClick={handleExport} size="sm">
          Экспорт в Excel
        </Button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          {STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>

        <select
          value={resourceType}
          onChange={(e) => setResourceType(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          {RESOURCE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>

        <input
          type="date"
          value={dateFrom}
          onChange={(e) => setDateFrom(e.target.value)}
          placeholder="Дата от"
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        />
        <input
          type="date"
          value={dateTo}
          onChange={(e) => setDateTo(e.target.value)}
          placeholder="Дата до"
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        />
      </div>

      <div className="mb-4">
        <input
          type="text"
          value={contragentId}
          onChange={(e) => setContragentId(e.target.value)}
          placeholder="ID контрагента (UUID)"
          className="h-9 w-80 rounded-md border border-input bg-background px-3 text-sm font-mono"
        />
      </div>

      {isLoading ? (
        <div className="py-12 text-center text-muted-foreground text-sm">Загрузка...</div>
      ) : contracts.length === 0 ? (
        <div className="py-12 text-center text-muted-foreground text-sm">Нет данных по заданным фильтрам</div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/50">
              <tr>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Номер</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Наименование</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Статус</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Ресурс</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">Сумма</th>
              </tr>
            </thead>
            <tbody>
              {contracts.map((c) => (
                <tr key={c.id} className="border-t border-border hover:bg-muted/30">
                  <td className="px-4 py-2.5 font-mono text-xs">{c.contract_number}</td>
                  <td className="px-4 py-2.5">{c.title}</td>
                  <td className="px-4 py-2.5">
                    <span className="rounded-full bg-muted px-2 py-0.5 text-xs">
                      {CONTRACT_STATUS_LABELS[c.status] ?? c.status}
                    </span>
                  </td>
                  <td className="px-4 py-2.5">{RESOURCE_TYPE_LABELS[c.resource_type] ?? c.resource_type}</td>
                  <td className="px-4 py-2.5 text-right font-mono text-xs">
                    {new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB' }).format(c.total_amount / 100)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
