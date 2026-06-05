'use client'

import { useState } from 'react'
import { useCan } from '@/shared/auth/hooks'
import {
  useTransactions,
  usePostTransaction,
  useReverseTransaction,
  TRANSACTION_TYPE_LABELS,
  TRANSACTION_STATUS_LABELS,
  TRANSACTION_TYPES,
  TRANSACTION_STATUSES,
  type Transaction,
  type TransactionPostRequest,
} from '@/shared/api/finance'
import { Button } from '@/shared/ui/button'
import { Input } from '@/shared/ui/input'

const STATUS_COLORS: Record<string, string> = {
  POSTED: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  REVERSED: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400',
}

const TYPE_COLORS: Record<string, string> = {
  PAYMENT: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  REFUND: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400',
  CORRECTION: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400',
}

function Badge({ label, colorClass }: { label: string; colorClass: string }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${colorClass}`}>
      {label}
    </span>
  )
}

function formatRub(kopecks: number) {
  return new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB' }).format(kopecks / 100)
}

function shortId(id: string) {
  return id.slice(0, 8) + '…'
}

function TransactionRow({
  tx,
  canWrite,
}: {
  tx: Transaction
  canWrite: boolean
}) {
  const reverse = useReverseTransaction()

  const handleReverse = () => {
    if (!confirm(`Сторнировать транзакцию ${shortId(tx.id)}?`)) return
    reverse.mutate(tx.id)
  }

  return (
    <tr className="border-b border-border hover:bg-muted/30 transition-colors">
      <td className="px-4 py-3 text-xs font-mono text-muted-foreground" title={tx.id}>
        {shortId(tx.id)}
      </td>
      <td className="px-4 py-3 text-xs font-mono text-muted-foreground" title={tx.contract_id}>
        {shortId(tx.contract_id)}
      </td>
      <td className="px-4 py-3">
        <Badge
          label={TRANSACTION_TYPE_LABELS[tx.transaction_type] ?? tx.transaction_type}
          colorClass={TYPE_COLORS[tx.transaction_type] ?? 'bg-muted text-muted-foreground'}
        />
      </td>
      <td className="px-4 py-3">
        <Badge
          label={TRANSACTION_STATUS_LABELS[tx.status] ?? tx.status}
          colorClass={STATUS_COLORS[tx.status] ?? 'bg-muted text-muted-foreground'}
        />
      </td>
      <td className="px-4 py-3 text-sm text-right font-mono">{formatRub(tx.amount)}</td>
      <td className="px-4 py-3 text-xs text-muted-foreground font-mono truncate max-w-40">
        {tx.external_ref || '—'}
      </td>
      <td className="px-4 py-3 text-right">
        {canWrite && tx.status === 'POSTED' && (
          <Button
            variant="ghost"
            size="sm"
            onClick={handleReverse}
            disabled={reverse.isPending}
            className="text-destructive hover:text-destructive"
          >
            Сторно
          </Button>
        )}
      </td>
    </tr>
  )
}

const EMPTY_FORM: TransactionPostRequest = {
  contract_id: '',
  amount: 0,
  transaction_type: 'PAYMENT',
  external_ref: '',
}

function PostTransactionForm({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState(EMPTY_FORM)
  const [amountRub, setAmountRub] = useState('')
  const [error, setError] = useState('')
  const post = usePostTransaction()

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    const rub = parseFloat(amountRub.replace(',', '.'))
    if (!form.contract_id.match(/^[0-9a-f-]{36}$/i)) {
      setError('Некорректный UUID договора')
      return
    }
    if (isNaN(rub) || rub <= 0) {
      setError('Некорректная сумма')
      return
    }

    post.mutate(
      { ...form, amount: Math.round(rub * 100) },
      {
        onSuccess: () => onClose(),
        onError: (err) => setError(err.message),
      },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="rounded-lg border border-border bg-card p-5 mb-6 space-y-4">
      <h2 className="text-sm font-semibold">Новая транзакция</h2>

      <div className="grid grid-cols-2 gap-4">
        <div className="col-span-2">
          <label className="block text-xs text-muted-foreground mb-1">UUID договора</label>
          <Input
            value={form.contract_id}
            onChange={(e) => setForm((f) => ({ ...f, contract_id: e.target.value.trim() }))}
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            required
          />
        </div>

        <div>
          <label className="block text-xs text-muted-foreground mb-1">Сумма (руб.)</label>
          <Input
            value={amountRub}
            onChange={(e) => setAmountRub(e.target.value)}
            placeholder="1 000.00"
            required
          />
        </div>

        <div>
          <label className="block text-xs text-muted-foreground mb-1">Тип</label>
          <select
            value={form.transaction_type}
            onChange={(e) => setForm((f) => ({ ...f, transaction_type: e.target.value }))}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {TRANSACTION_TYPES.map((t) => (
              <option key={t} value={t}>{TRANSACTION_TYPE_LABELS[t]}</option>
            ))}
          </select>
        </div>

        <div className="col-span-2">
          <label className="block text-xs text-muted-foreground mb-1">Внешний референс (необязательно)</label>
          <Input
            value={form.external_ref}
            onChange={(e) => setForm((f) => ({ ...f, external_ref: e.target.value }))}
            placeholder="PAY-2026-001"
          />
        </div>
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}

      <div className="flex gap-2 justify-end">
        <Button type="button" variant="outline" size="sm" onClick={onClose}>
          Отмена
        </Button>
        <Button type="submit" size="sm" disabled={post.isPending}>
          {post.isPending ? 'Сохранение…' : 'Провести'}
        </Button>
      </div>
    </form>
  )
}

export default function FinancePage() {
  const canCreate = useCan('finance.create')
  const canWrite = useCan('finance.write')

  const [showForm, setShowForm] = useState(false)
  const [typeFilter, setTypeFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [contractFilter, setContractFilter] = useState('')
  const [contractFilterInput, setContractFilterInput] = useState('')
  const [cursors, setCursors] = useState<string[]>([])
  const currentCursor = cursors.at(-1)

  const { data, isLoading, isError } = useTransactions({
    cursor: currentCursor,
    limit: 20,
    transaction_type: typeFilter || undefined,
    status: statusFilter || undefined,
    contract_id: contractFilter || undefined,
  })

  const resetCursors = () => setCursors([])

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Финансы</h1>
        {canCreate && !showForm && (
          <Button size="sm" onClick={() => setShowForm(true)}>
            Провести транзакцию
          </Button>
        )}
      </div>

      {showForm && (
        <PostTransactionForm onClose={() => setShowForm(false)} />
      )}

      <div className="mb-4 flex flex-wrap gap-3">
        <select
          value={typeFilter}
          onChange={(e) => { setTypeFilter(e.target.value); resetCursors() }}
          className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <option value="">Все типы</option>
          {TRANSACTION_TYPES.map((t) => (
            <option key={t} value={t}>{TRANSACTION_TYPE_LABELS[t]}</option>
          ))}
        </select>

        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); resetCursors() }}
          className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <option value="">Все статусы</option>
          {TRANSACTION_STATUSES.map((s) => (
            <option key={s} value={s}>{TRANSACTION_STATUS_LABELS[s]}</option>
          ))}
        </select>

        <div className="flex gap-1">
          <Input
            value={contractFilterInput}
            onChange={(e) => setContractFilterInput(e.target.value)}
            placeholder="UUID договора…"
            className="w-72 text-xs"
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                setContractFilter(contractFilterInput.trim())
                resetCursors()
              }
            }}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() => { setContractFilter(contractFilterInput.trim()); resetCursors() }}
          >
            Найти
          </Button>
          {contractFilter && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => { setContractFilter(''); setContractFilterInput(''); resetCursors() }}
            >
              ✕
            </Button>
          )}
        </div>
      </div>

      {isError && (
        <p className="text-sm text-destructive mb-4">Ошибка загрузки транзакций</p>
      )}

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Загрузка...</p>
      ) : (
        <>
          <div className="rounded-md border border-border overflow-hidden">
            <table className="w-full">
              <thead className="bg-muted/50">
                <tr>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">ID транзакции</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">ID договора</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Тип</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Статус</th>
                  <th className="px-4 py-2.5 text-right text-xs font-medium text-muted-foreground">Сумма</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Референс</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {data?.results.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-sm text-muted-foreground">
                      Транзакции не найдены
                    </td>
                  </tr>
                ) : (
                  data?.results.map((tx) => (
                    <TransactionRow key={tx.id} tx={tx} canWrite={canWrite} />
                  ))
                )}
              </tbody>
            </table>
          </div>

          {data && (
            <div className="mt-3 flex items-center gap-3">
              <span className="text-xs text-muted-foreground">
                Всего: {data.count}
              </span>
              <div className="flex gap-2">
                {cursors.length > 0 && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setCursors((prev) => prev.slice(0, -1))}
                  >
                    ← Назад
                  </Button>
                )}
                {data.next_cursor && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setCursors((prev) => [...prev, data.next_cursor!])}
                  >
                    Вперёд →
                  </Button>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
