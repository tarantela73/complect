'use client'

import { useState } from 'react'
import Link from 'next/link'
import {
  useReviewQueue,
  useLineCandidates,
  useManualMatch,
  useDetachMatch,
  type ReviewLine,
  type TxCandidate,
} from '@/shared/api/payments'

// ── Utilities ─────────────────────────────────────────────────────────────────

function fmtMoney(val: string | null | undefined, currency: string) {
  if (!val) return '—'
  const n = parseFloat(val)
  return `${n.toLocaleString('ru-RU', { minimumFractionDigits: 2 })} ${currency}`
}

function directionBadge(d: string) {
  if (d === 'credit') return <span className="text-green-600 dark:text-green-400 font-mono">▲</span>
  if (d === 'debit')  return <span className="text-red-600 dark:text-red-400 font-mono">▼</span>
  return <span className="text-muted-foreground">—</span>
}

// ── Match status badge ─────────────────────────────────────────────────────────

const MATCH_STYLES: Record<string, { text: string; cls: string }> = {
  MATCHED:   { text: 'Сопост.',   cls: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400' },
  PARTIAL:   { text: 'Частично',  cls: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400' },
  AMBIGUOUS: { text: 'Неоднозн.', cls: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400' },
  UNMATCHED: { text: 'Нет',       cls: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400' },
}

function MatchBadge({ status }: { status: string }) {
  const s = MATCH_STYLES[status] ?? { text: status, cls: 'bg-muted text-muted-foreground' }
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${s.cls}`}>
      {s.text}
    </span>
  )
}

// ── Manual match dialog ───────────────────────────────────────────────────────

function ManualMatchDialog({
  line,
  onClose,
}: {
  line: ReviewLine
  onClose: () => void
}) {
  const [txId, setTxId] = useState('')
  const [err, setErr]   = useState('')
  const stmtId = line.statement_id
  const lineId = line.id

  const { data: candidates, isLoading } = useLineCandidates(stmtId, lineId, true)
  const attach = useManualMatch()
  const detach = useDetachMatch()

  const handleAttach = async (transactionId: string) => {
    setErr('')
    try {
      await attach.mutateAsync({ stmtId, lineId, transactionId })
      onClose()
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Ошибка привязки')
    }
  }

  const handleDetach = async () => {
    setErr('')
    try {
      await detach.mutateAsync({ stmtId, lineId })
      onClose()
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Ошибка отвязки')
    }
  }

  const isMatched = line.match_status === 'MATCHED'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-full max-w-lg rounded-lg border border-border bg-card p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold">
            {isMatched ? 'Сопоставление строки' : 'Привязать к транзакции'}
          </h3>
          <button onClick={onClose} className="text-lg leading-none text-muted-foreground hover:text-foreground">×</button>
        </div>

        <div className="mb-4 rounded-md bg-muted/40 px-3 py-2 text-xs">
          <span className="font-mono">{line.transaction_date ?? '—'}</span>
          {' · '}
          <span className="font-mono font-medium">
            {line.amount ? parseFloat(line.amount).toLocaleString('ru-RU', { minimumFractionDigits: 2 }) : '—'}
          </span>
          {' · '}
          <span className="text-muted-foreground">{line.counterparty_name || line.counterparty_inn || '—'}</span>
          {' · '}
          <span className="text-muted-foreground font-mono">{line.statement_file_name}</span>
        </div>

        {isMatched && line.transaction_id && (
          <div className="mb-4">
            <p className="mb-1.5 text-xs text-muted-foreground">Привязана к транзакции:</p>
            <p className="rounded bg-muted px-2 py-1 font-mono text-xs">{line.transaction_id}</p>
            <button
              onClick={handleDetach}
              disabled={detach.isPending}
              className="mt-3 rounded-md border border-destructive/40 px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
            >
              {detach.isPending ? 'Отвязка...' : 'Отвязать'}
            </button>
          </div>
        )}

        {!isMatched && (
          <>
            {isLoading && <p className="mb-3 text-xs text-muted-foreground">Поиск кандидатов...</p>}

            {!isLoading && candidates && candidates.length > 0 && (
              <div className="mb-4">
                <p className="mb-2 text-xs text-muted-foreground">Найденные совпадения:</p>
                <div className="max-h-48 space-y-1 overflow-y-auto">
                  {candidates.map((c: TxCandidate) => (
                    <button
                      key={c.transaction_id}
                      onClick={() => handleAttach(c.transaction_id)}
                      disabled={attach.isPending}
                      className="w-full rounded border border-border px-3 py-2 text-left text-xs transition-colors hover:bg-accent/50 disabled:opacity-50"
                    >
                      <div className="flex justify-between gap-2">
                        <span className="truncate font-mono text-muted-foreground">{c.transaction_id}</span>
                        <span className="shrink-0 font-mono font-medium">
                          {(c.amount_kopecks / 100).toLocaleString('ru-RU', { minimumFractionDigits: 2 })}
                        </span>
                      </div>
                      {(c.external_ref || c.counterparty_inn) && (
                        <div className="mt-0.5 text-muted-foreground">
                          {c.external_ref && <span>{c.external_ref}</span>}
                          {c.counterparty_inn && <span className="ml-2">ИНН: {c.counterparty_inn}</span>}
                        </div>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {!isLoading && candidates?.length === 0 && (
              <p className="mb-3 text-xs text-muted-foreground">Автоматические кандидаты не найдены.</p>
            )}

            <div>
              <p className="mb-1.5 text-xs text-muted-foreground">Или введите ID транзакции вручную:</p>
              <div className="flex gap-2">
                <input
                  value={txId}
                  onChange={(e) => setTxId(e.target.value)}
                  placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                  className="flex-1 rounded-md border border-border bg-background px-3 py-1.5 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-primary"
                />
                <button
                  onClick={() => { if (txId) handleAttach(txId) }}
                  disabled={!txId || attach.isPending}
                  className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {attach.isPending ? '...' : 'Привязать'}
                </button>
              </div>
            </div>
          </>
        )}

        {err && <p className="mt-3 text-xs text-destructive">{err}</p>}
      </div>
    </div>
  )
}

// ── Filter tabs ───────────────────────────────────────────────────────────────

type Filter = '' | 'UNMATCHED' | 'AMBIGUOUS' | 'PARTIAL'

function FilterTab({
  label, count, active, onClick,
}: { label: string; count: number; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
        active ? 'bg-primary text-primary-foreground' : 'border border-border hover:bg-accent'
      }`}
    >
      {label} <span className={active ? 'opacity-75' : 'text-muted-foreground'}>({count})</span>
    </button>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ReviewQueuePage() {
  const [filter, setFilter]     = useState<Filter>('')
  const [matchingLine, setMatchingLine] = useState<ReviewLine | null>(null)

  const { data, isLoading, error, refetch } = useReviewQueue(filter || undefined)

  const items = data?.items ?? []

  const actionLabel = (status: string) => {
    if (status === 'AMBIGUOUS') return 'Выбрать'
    if (status === 'PARTIAL')   return 'Доуточнить'
    return 'Привязать'
  }

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Link
              href="/dashboard/payments"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              Выписки
            </Link>
            <span className="text-muted-foreground">/</span>
            <h1 className="text-xl font-semibold">Очередь сверки</h1>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Строки импортированных выписок, требующие ручного сопоставления
          </p>
        </div>
        <button
          onClick={() => refetch()}
          className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-accent transition-colors"
        >
          Обновить
        </button>
      </div>

      {/* Stats summary */}
      {data && (
        <div className="mb-5 flex flex-wrap gap-3">
          <div className="rounded-lg border border-border bg-card px-4 py-3 text-sm">
            <span className="text-muted-foreground">Всего: </span>
            <span className="font-semibold">{data.total_count}</span>
          </div>
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm dark:border-red-900/40 dark:bg-red-950/20">
            <span className="text-red-700 dark:text-red-400">UNMATCHED: </span>
            <span className="font-semibold text-red-800 dark:text-red-300">{data.unmatched_count}</span>
          </div>
          <div className="rounded-lg border border-orange-200 bg-orange-50 px-4 py-3 text-sm dark:border-orange-900/40 dark:bg-orange-950/20">
            <span className="text-orange-700 dark:text-orange-400">AMBIGUOUS: </span>
            <span className="font-semibold text-orange-800 dark:text-orange-300">{data.ambiguous_count}</span>
          </div>
          <div className="rounded-lg border border-yellow-200 bg-yellow-50 px-4 py-3 text-sm dark:border-yellow-900/40 dark:bg-yellow-950/20">
            <span className="text-yellow-700 dark:text-yellow-400">PARTIAL: </span>
            <span className="font-semibold text-yellow-800 dark:text-yellow-300">{data.partial_count}</span>
          </div>
        </div>
      )}

      {/* Filter tabs */}
      {data && (
        <div className="mb-4 flex flex-wrap gap-2">
          <FilterTab label="Все" count={data.total_count} active={filter === ''} onClick={() => setFilter('')} />
          <FilterTab label="Не сопост." count={data.unmatched_count} active={filter === 'UNMATCHED'} onClick={() => setFilter('UNMATCHED')} />
          <FilterTab label="Неоднозн." count={data.ambiguous_count} active={filter === 'AMBIGUOUS'} onClick={() => setFilter('AMBIGUOUS')} />
          <FilterTab label="Частично" count={data.partial_count} active={filter === 'PARTIAL'} onClick={() => setFilter('PARTIAL')} />
        </div>
      )}

      {isLoading && <p className="text-sm text-muted-foreground">Загрузка...</p>}
      {error    && <p className="text-sm text-destructive">Ошибка загрузки очереди.</p>}

      {!isLoading && !error && items.length === 0 && (
        <div className="rounded-lg border border-border bg-card p-10 text-center">
          <p className="text-sm font-medium text-green-700 dark:text-green-400">
            Все строки сопоставлены
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Нет строк, требующих ручной обработки.
          </p>
        </div>
      )}

      {items.length > 0 && (
        <div className="rounded-lg border border-border bg-card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="border-b border-border bg-muted/30">
                <tr className="text-left text-muted-foreground">
                  <th className="px-3 py-2 font-medium">Дата</th>
                  <th className="px-3 py-2 font-medium">Сумма</th>
                  <th className="px-3 py-2 font-medium w-4"></th>
                  <th className="px-3 py-2 font-medium">Контрагент</th>
                  <th className="px-3 py-2 font-medium">ИНН</th>
                  <th className="px-3 py-2 font-medium">Назначение</th>
                  <th className="px-3 py-2 font-medium">Выписка</th>
                  <th className="px-3 py-2 font-medium">Статус</th>
                  <th className="px-3 py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {items.map((line: ReviewLine) => (
                  <tr key={line.id} className="hover:bg-muted/20 transition-colors align-top">
                    <td className="px-3 py-2 font-mono whitespace-nowrap">{line.transaction_date ?? '—'}</td>
                    <td className="px-3 py-2 font-mono whitespace-nowrap">
                      {line.amount ? parseFloat(line.amount).toLocaleString('ru-RU', { minimumFractionDigits: 2 }) : '—'}
                      {' '}
                      <span className="text-muted-foreground">{line.currency}</span>
                    </td>
                    <td className="px-3 py-2">{directionBadge(line.direction)}</td>
                    <td className="px-3 py-2 max-w-44 truncate" title={line.counterparty_name}>
                      {line.counterparty_name || '—'}
                    </td>
                    <td className="px-3 py-2 font-mono text-muted-foreground">{line.counterparty_inn || '—'}</td>
                    <td className="px-3 py-2 max-w-64 truncate text-muted-foreground" title={line.description}>
                      {line.description || '—'}
                    </td>
                    <td className="px-3 py-2">
                      <div className="font-mono text-muted-foreground truncate max-w-32" title={line.statement_file_name}>
                        {line.statement_file_name}
                      </div>
                      {line.statement_period_from && (
                        <div className="text-muted-foreground/60">
                          {line.statement_period_from}
                          {line.statement_period_to ? ` — ${line.statement_period_to}` : ''}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <MatchBadge status={line.match_status} />
                    </td>
                    <td className="px-3 py-2">
                      <button
                        onClick={() => setMatchingLine(line)}
                        className="rounded px-2 py-0.5 text-xs text-primary hover:bg-primary/10 transition-colors whitespace-nowrap"
                      >
                        {actionLabel(line.match_status)}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {matchingLine && (
        <ManualMatchDialog
          line={matchingLine}
          onClose={() => { setMatchingLine(null); refetch() }}
        />
      )}
    </div>
  )
}
