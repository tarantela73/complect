'use client'

import { useState } from 'react'
import { useReviewQueue } from '@/shared/api/payments'
import { downloadReport } from '@/shared/api/reports'
import { Button } from '@/shared/ui/button'

const MATCH_STATUS_OPTIONS = [
  { value: '', label: 'Все статусы' },
  { value: 'UNMATCHED', label: 'Не сопоставлена' },
  { value: 'PARTIAL', label: 'Частично' },
  { value: 'MATCHED', label: 'Сопоставлена' },
  { value: 'AMBIGUOUS', label: 'Неоднозначно' },
]

const MATCH_STATUS_LABELS: Record<string, string> = {
  UNMATCHED: 'Не сопоставлена',
  PARTIAL: 'Частично',
  MATCHED: 'Сопоставлена',
  AMBIGUOUS: 'Неоднозначно',
}

export default function PaymentsReportPage() {
  const [matchStatus, setMatchStatus] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [statementId, setStatementId] = useState('')

  const { data, isLoading } = useReviewQueue(matchStatus || undefined)
  const lines = data?.items ?? []
  const unmatchedCount = data?.unmatched_count ?? lines.filter((l) => l.match_status === 'UNMATCHED').length

  const [exportError, setExportError] = useState('')

  async function handleExport() {
    setExportError('')
    try {
      const p = new URLSearchParams()
      if (matchStatus) p.set('match_status', matchStatus)
      if (dateFrom) p.set('date_from', dateFrom)
      if (dateTo) p.set('date_to', dateTo)
      if (statementId) p.set('statement_id', statementId)
      const qs = p.toString()
      await downloadReport(`/api/v1/reports/payments/export/${qs ? '?' + qs : ''}`, 'payments_report.xlsx')
    } catch {
      setExportError('Ошибка экспорта. Попробуйте снова.')
    }
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold">Отчёт: Банковские выписки</h1>
          <p className="text-muted-foreground text-sm mt-0.5">Превью из очереди сверки</p>
        </div>
        <Button onClick={handleExport} size="sm">Экспорт в Excel</Button>
      </div>
      {exportError && <p className="text-sm text-destructive mb-4">{exportError}</p>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <select value={matchStatus} onChange={(e) => setMatchStatus(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm">
          {MATCH_STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm" />
        <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm" />
        <input type="text" value={statementId} onChange={(e) => setStatementId(e.target.value)}
          placeholder="ID выписки (UUID)" className="h-9 rounded-md border border-input bg-background px-3 text-sm font-mono" />
      </div>

      {unmatchedCount > 0 && (
        <div className="mb-4 inline-flex items-center gap-1.5 rounded-md bg-yellow-50 border border-yellow-200 px-3 py-1.5 text-sm text-yellow-800 dark:bg-yellow-900/20 dark:border-yellow-800 dark:text-yellow-300">
          <span className="font-semibold">{unmatchedCount}</span> несопоставленных строк
        </div>
      )}

      {isLoading ? (
        <div className="py-12 text-center text-muted-foreground text-sm">Загрузка...</div>
      ) : lines.length === 0 ? (
        <div className="py-12 text-center text-muted-foreground text-sm">Нет строк по заданным фильтрам</div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/50">
              <tr>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Дата</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">Сумма</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Контрагент</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Статус матчинга</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">Уверенность</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l) => (
                <tr key={l.id} className="border-t border-border hover:bg-muted/30">
                  <td className="px-4 py-2.5 text-xs">{l.transaction_date ?? '—'}</td>
                  <td className="px-4 py-2.5 text-right font-mono text-xs">
                    {l.amount != null ? new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB' }).format(Number(l.amount)) : '—'}
                  </td>
                  <td className="px-4 py-2.5 text-xs">{l.counterparty_name || l.counterparty_inn || '—'}</td>
                  <td className="px-4 py-2.5">
                    <span className={`rounded-full px-2 py-0.5 text-xs ${
                      l.match_status === 'MATCHED' ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400' :
                      l.match_status === 'UNMATCHED' ? 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400' :
                      'bg-muted text-muted-foreground'
                    }`}>
                      {MATCH_STATUS_LABELS[l.match_status] ?? l.match_status}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right text-xs">{l.match_confidence}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
