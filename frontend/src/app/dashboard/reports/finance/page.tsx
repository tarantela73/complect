'use client'

import { useState } from 'react'
import { useTransactions, TRANSACTION_TYPE_LABELS, TRANSACTION_STATUS_LABELS } from '@/shared/api/finance'
import { downloadReport } from '@/shared/api/reports'
import { Button } from '@/shared/ui/button'

const TYPE_OPTIONS = [
  { value: '', label: 'Все типы' },
  ...Object.entries(TRANSACTION_TYPE_LABELS).map(([v, l]) => ({ value: v, label: l })),
]

const STATUS_OPTIONS = [
  { value: '', label: 'Все статусы' },
  ...Object.entries(TRANSACTION_STATUS_LABELS).map(([v, l]) => ({ value: v, label: l })),
]

export default function FinanceReportPage() {
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [txnType, setTxnType] = useState('')
  const [status, setStatus] = useState('')

  const { data, isLoading } = useTransactions({
    transaction_type: txnType || undefined,
    status: status || undefined,
    limit: 20,
  })

  const transactions = data?.results ?? []

  const [exportError, setExportError] = useState('')

  async function handleExport() {
    setExportError('')
    try {
      const p = new URLSearchParams()
      if (dateFrom) p.set('date_from', dateFrom)
      if (dateTo) p.set('date_to', dateTo)
      if (txnType) p.set('transaction_type', txnType)
      if (status) p.set('status', status)
      const qs = p.toString()
      await downloadReport(`/api/v1/reports/finance/export/${qs ? '?' + qs : ''}`, 'finance_report.xlsx')
    } catch {
      setExportError('Ошибка экспорта. Попробуйте снова.')
    }
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold">Отчёт: Финансы</h1>
          <p className="text-muted-foreground text-sm mt-0.5">Превью — первые 20 транзакций</p>
        </div>
        <Button onClick={handleExport} size="sm">Экспорт в Excel</Button>
      </div>
      {exportError && <p className="text-sm text-destructive mb-4">{exportError}</p>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm" placeholder="Дата от" />
        <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm" placeholder="Дата до" />
        <select value={txnType} onChange={(e) => setTxnType(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm">
          {TYPE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm">
          {STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>

      {isLoading ? (
        <div className="py-12 text-center text-muted-foreground text-sm">Загрузка...</div>
      ) : transactions.length === 0 ? (
        <div className="py-12 text-center text-muted-foreground text-sm">Нет транзакций по заданным фильтрам</div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/50">
              <tr>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Дата</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Тип</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Статус</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">Сумма</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Внешний реф.</th>
              </tr>
            </thead>
            <tbody>
              {transactions.map((t) => (
                <tr key={t.id} className="border-t border-border hover:bg-muted/30">
                  <td className="px-4 py-2.5 text-xs font-mono">{t.external_ref || t.contract_id.slice(0, 8)}</td>
                  <td className="px-4 py-2.5 text-xs">{TRANSACTION_TYPE_LABELS[t.transaction_type] ?? t.transaction_type}</td>
                  <td className="px-4 py-2.5">
                    <span className="rounded-full bg-muted px-2 py-0.5 text-xs">
                      {TRANSACTION_STATUS_LABELS[t.status] ?? t.status}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-xs">
                    {new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB' }).format(t.amount / 100)}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground font-mono">{t.external_ref || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
