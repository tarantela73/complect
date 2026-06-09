'use client'

import { useRef, useState } from 'react'
import {
  useBankStatements,
  useBankStatement,
  useUploadStatement,
  useConfirmStatement,
  useCancelStatement,
  useLineCandidates,
  useManualMatch,
  useDetachMatch,
  type BankStatement,
  type BankStatementLine,
  type TxCandidate,
} from '@/shared/api/payments'

// ── Utilities ─────────────────────────────────────────────────────────────────

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('ru-RU', {
    timeZone: 'Europe/Moscow',
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function fmtMoney(val: string | null, currency: string) {
  if (!val) return '—'
  const n = parseFloat(val)
  return `${n.toLocaleString('ru-RU', { minimumFractionDigits: 2 })} ${currency}`
}

function statusLabel(s: BankStatement['status']): { text: string; cls: string; pulse?: boolean } {
  const map: Record<string, { text: string; cls: string; pulse?: boolean }> = {
    pending:      { text: 'Загружен',        cls: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400' },
    parsed:       { text: 'Распознан',       cls: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400' },
    parse_failed: { text: 'Ошибка',          cls: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400' },
    importing:    { text: 'Сопоставление',   cls: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400', pulse: true },
    imported:     { text: 'Импортирован',    cls: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400' },
    cancelled:    { text: 'Отменён',         cls: 'bg-muted text-muted-foreground' },
  }
  return map[s] ?? { text: s, cls: 'bg-muted text-muted-foreground' }
}

function StatusBadge({ stmt }: { stmt: BankStatement }) {
  const sl = statusLabel(stmt.status)
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${sl.cls}`}>
      {sl.pulse && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-75" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-blue-500" />
        </span>
      )}
      {sl.text}
    </span>
  )
}

function directionBadge(d: string) {
  if (d === 'credit') return <span className="text-green-600 dark:text-green-400 font-mono">▲</span>
  if (d === 'debit')  return <span className="text-red-600 dark:text-red-400 font-mono">▼</span>
  return <span className="text-muted-foreground">—</span>
}

function MatchBadge({ status, confidence }: { status: string; confidence: number }) {
  const map: Record<string, { text: string; cls: string }> = {
    MATCHED:   { text: 'Сопост.',   cls: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400' },
    PARTIAL:   { text: 'Частично',  cls: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400' },
    AMBIGUOUS: { text: 'Неоднозн.', cls: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400' },
    UNMATCHED: { text: 'Нет',       cls: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400' },
  }
  const s = map[status] ?? { text: status, cls: 'bg-muted text-muted-foreground' }
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${s.cls}`}>
      {s.text}
      {confidence > 0 && <span className="opacity-60">{confidence}%</span>}
    </span>
  )
}

// ── Manual match dialog ───────────────────────────────────────────────────────

function ManualMatchDialog({
  stmtId,
  line,
  onClose,
}: {
  stmtId: string
  line: BankStatementLine
  onClose: () => void
}) {
  const [txId, setTxId] = useState('')
  const [err, setErr] = useState('')
  const { data: candidates, isLoading } = useLineCandidates(stmtId, line.id, true)
  const attach = useManualMatch()
  const detach = useDetachMatch()

  const handleAttach = async (transactionId: string) => {
    setErr('')
    try {
      await attach.mutateAsync({ stmtId, lineId: line.id, transactionId })
      onClose()
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Ошибка привязки')
    }
  }

  const handleDetach = async () => {
    setErr('')
    try {
      await detach.mutateAsync({ stmtId, lineId: line.id })
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

        {/* Line summary */}
        <div className="mb-4 rounded-md bg-muted/40 px-3 py-2 text-xs">
          <span className="font-mono">{line.transaction_date ?? '—'}</span>
          {' · '}
          <span className="font-mono font-medium">
            {line.amount ? parseFloat(line.amount).toLocaleString('ru-RU', { minimumFractionDigits: 2 }) : '—'}
          </span>
          {' · '}
          <span className="text-muted-foreground">{line.counterparty_name || line.counterparty_inn || '—'}</span>
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

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <h2 className="mb-4 text-xs font-semibold uppercase tracking-widest text-muted-foreground">
        {title}
      </h2>
      {children}
    </div>
  )
}

// ── Upload zone ───────────────────────────────────────────────────────────────

function UploadCard({ onUploaded }: { onUploaded: (id: string) => void }) {
  const fileRef = useRef<HTMLInputElement>(null)
  const upload = useUploadStatement()
  const [err, setErr] = useState('')

  const handleFile = async (file: File) => {
    setErr('')
    try {
      const stmt = await upload.mutateAsync(file)
      onUploaded(stmt.id)
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Ошибка загрузки')
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }

  return (
    <Card title="Загрузить выписку">
      <div
        onDrop={handleDrop}
        onDragOver={(e) => e.preventDefault()}
        className="flex flex-col items-center justify-center rounded-lg border-2 border-dashed border-border p-10 text-center cursor-pointer hover:border-primary/50 transition-colors"
        onClick={() => fileRef.current?.click()}
      >
        <svg className="mb-3 h-10 w-10 text-muted-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 13h6m-3-3v6m5 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
        <p className="text-sm font-medium">
          {upload.isPending ? 'Загрузка...' : 'Перетащите файл или нажмите для выбора'}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">Формат: 1C Client Bank Exchange (КЛ→1С, .txt)</p>
      </div>
      <input
        ref={fileRef}
        type="file"
        accept=".txt,.1c"
        className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f) }}
      />
      {err && <p className="mt-2 text-xs text-destructive">{err}</p>}
    </Card>
  )
}

// ── Importing banner ──────────────────────────────────────────────────────────

function ImportingBanner({ stmt }: { stmt: BankStatement }) {
  return (
    <div className="mt-4 rounded-lg border border-blue-200 bg-blue-50 px-4 py-4 dark:border-blue-900/40 dark:bg-blue-950/20">
      <div className="flex items-center gap-3">
        <svg className="h-5 w-5 animate-spin text-blue-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        <div>
          <p className="text-sm font-medium text-blue-900 dark:text-blue-200">Сопоставление выполняется...</p>
          <p className="mt-0.5 text-xs text-blue-700 dark:text-blue-400">
            {stmt.total_lines
              ? `${stmt.total_lines} строк передано в обработку. Страница обновится автоматически.`
              : 'Строки передаются в обработку. Страница обновится автоматически.'}
          </p>
        </div>
      </div>
    </div>
  )
}

// ── Lines table ───────────────────────────────────────────────────────────────

type LinesFilter = 'all' | 'review'

function LinesTable({ stmtId, defaultFilter = 'all' }: { stmtId: string; defaultFilter?: LinesFilter }) {
  const { data, isLoading } = useBankStatement(stmtId)
  const [matchingLine, setMatchingLine] = useState<BankStatementLine | null>(null)
  const [filter, setFilter] = useState<LinesFilter>(defaultFilter)

  if (isLoading) return <p className="text-sm text-muted-foreground">Загрузка строк...</p>
  if (!data) return null

  const lines = data.lines ?? []
  const parsed = lines.filter((l) => l.parse_status === 'parsed')
  const failed  = lines.filter((l) => l.parse_status === 'failed')

  const matchStats = {
    MATCHED:   parsed.filter((l) => l.match_status === 'MATCHED').length,
    PARTIAL:   parsed.filter((l) => l.match_status === 'PARTIAL').length,
    AMBIGUOUS: parsed.filter((l) => l.match_status === 'AMBIGUOUS').length,
    UNMATCHED: parsed.filter((l) => l.match_status === 'UNMATCHED').length,
  }

  const needsReview = matchStats.PARTIAL + matchStats.AMBIGUOUS + matchStats.UNMATCHED
  const displayLines = filter === 'review'
    ? parsed.filter((l) => l.match_status !== 'MATCHED')
    : parsed

  return (
    <div>
      {failed.length > 0 && (
        <div className="mb-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {failed.length} строк не распознано — они пропущены при импорте.
        </div>
      )}

      {parsed.length > 0 && (
        <div className="mb-3 flex items-center justify-between gap-2">
          <div className="flex flex-wrap gap-2 text-xs">
            {matchStats.MATCHED > 0 && <><MatchBadge status="MATCHED" confidence={0} /><span className="text-muted-foreground">{matchStats.MATCHED}</span></>}
            {matchStats.PARTIAL > 0 && <><MatchBadge status="PARTIAL" confidence={0} /><span className="text-muted-foreground">{matchStats.PARTIAL}</span></>}
            {matchStats.AMBIGUOUS > 0 && <><MatchBadge status="AMBIGUOUS" confidence={0} /><span className="text-muted-foreground">{matchStats.AMBIGUOUS}</span></>}
            {matchStats.UNMATCHED > 0 && <><MatchBadge status="UNMATCHED" confidence={0} /><span className="text-muted-foreground">{matchStats.UNMATCHED}</span></>}
          </div>
          {needsReview > 0 && (
            <div className="flex gap-1 text-xs">
              <button
                onClick={() => setFilter('all')}
                className={`rounded-md px-2 py-1 transition-colors ${filter === 'all' ? 'bg-primary text-primary-foreground' : 'border border-border hover:bg-accent'}`}
              >
                Все ({parsed.length})
              </button>
              <button
                onClick={() => setFilter('review')}
                className={`rounded-md px-2 py-1 transition-colors ${filter === 'review' ? 'bg-primary text-primary-foreground' : 'border border-border hover:bg-accent'}`}
              >
                Требуют внимания ({needsReview})
              </button>
            </div>
          )}
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th className="pb-2 pr-3 font-medium">#</th>
              <th className="pb-2 pr-3 font-medium">Дата</th>
              <th className="pb-2 pr-3 font-medium">Сумма</th>
              <th className="pb-2 pr-3 font-medium"></th>
              <th className="pb-2 pr-3 font-medium">Контрагент</th>
              <th className="pb-2 pr-3 font-medium">ИНН</th>
              <th className="pb-2 pr-3 font-medium">Назначение</th>
              <th className="pb-2 pr-3 font-medium">№ doc</th>
              <th className="pb-2 pr-3 font-medium">Матч</th>
              <th className="pb-2 font-medium"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {displayLines.map((l: BankStatementLine) => (
              <tr key={l.id} className="align-top">
                <td className="py-1.5 pr-3 font-mono text-muted-foreground">{l.line_number + 1}</td>
                <td className="py-1.5 pr-3 font-mono whitespace-nowrap">{l.transaction_date ?? '—'}</td>
                <td className="py-1.5 pr-3 font-mono whitespace-nowrap">
                  {l.amount ? parseFloat(l.amount).toLocaleString('ru-RU', { minimumFractionDigits: 2 }) : '—'}
                </td>
                <td className="py-1.5 pr-3">{directionBadge(l.direction)}</td>
                <td className="py-1.5 pr-3 max-w-50 truncate" title={l.counterparty_name}>{l.counterparty_name || '—'}</td>
                <td className="py-1.5 pr-3 font-mono">{l.counterparty_inn || '—'}</td>
                <td className="py-1.5 pr-3 max-w-70 truncate text-muted-foreground" title={l.description}>{l.description || '—'}</td>
                <td className="py-1.5 pr-3 font-mono text-muted-foreground">{l.doc_number || '—'}</td>
                <td className="py-1.5 pr-3 whitespace-nowrap">
                  <MatchBadge status={l.match_status} confidence={l.match_confidence} />
                </td>
                <td className="py-1.5">
                  <button
                    onClick={() => setMatchingLine(l)}
                    className="rounded px-2 py-0.5 text-xs text-primary hover:bg-primary/10 transition-colors"
                  >
                    {l.match_status === 'MATCHED' ? 'Изменить' : l.match_status === 'AMBIGUOUS' ? 'Выбрать' : 'Привязать'}
                  </button>
                </td>
              </tr>
            ))}
            {failed.map((l: BankStatementLine) => (
              <tr key={l.id} className="align-top bg-destructive/5">
                <td className="py-1.5 pr-3 font-mono text-muted-foreground">{l.line_number + 1}</td>
                <td className="py-1.5 pr-3 text-destructive" colSpan={9}>
                  <span className="font-medium">Ошибка: </span>{l.parse_error || 'неизвестная ошибка'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {displayLines.length === 0 && failed.length === 0 && (
          <p className="py-4 text-center text-sm text-muted-foreground">
            {filter === 'review' ? 'Все строки успешно сопоставлены.' : 'Нет строк для отображения.'}
          </p>
        )}
      </div>

      {matchingLine && (
        <ManualMatchDialog
          stmtId={stmtId}
          line={matchingLine}
          onClose={() => setMatchingLine(null)}
        />
      )}
    </div>
  )
}

// ── Statement detail panel ────────────────────────────────────────────────────

function StatementPanel({ stmt, onClose }: { stmt: BankStatement; onClose: () => void }) {
  const confirm = useConfirmStatement()
  const cancel  = useCancelStatement()
  const [err, setErr] = useState('')

  const handleConfirm = async () => {
    setErr('')
    try {
      await confirm.mutateAsync(stmt.id)
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Ошибка')
    }
  }

  const handleCancel = async () => {
    if (!window.confirm('Отменить импорт выписки?')) return
    setErr('')
    try {
      await cancel.mutateAsync(stmt.id)
      onClose()
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Ошибка')
    }
  }

  const canConfirm = stmt.status === 'parsed'
  const canCancel  = stmt.status === 'parsed' || stmt.status === 'parse_failed'
  const isImporting = stmt.status === 'importing'
  const isImported  = stmt.status === 'imported'

  const needsReviewCount = isImported
    ? (stmt.total_lines ?? 0) - (stmt.matched_lines ?? 0) - (stmt.failed_lines ?? 0)
    : 0

  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-medium">{stmt.file_name}</span>
            <StatusBadge stmt={stmt} />
          </div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {stmt.account_number && <span>Счёт: {stmt.account_number}</span>}
            {stmt.period_from && stmt.period_to && <span>{stmt.period_from} — {stmt.period_to}</span>}
            {stmt.opening_balance && <span>Открытие: {fmtMoney(stmt.opening_balance, stmt.currency)}</span>}
            {stmt.closing_balance && <span>Закрытие: {fmtMoney(stmt.closing_balance, stmt.currency)}</span>}
            {isImported && stmt.matched_lines !== null && stmt.matched_lines !== undefined ? (
              <span>
                Строк: {stmt.total_lines ?? 0} · Сопоставлено: {stmt.matched_lines}
                {needsReviewCount > 0 && (
                  <span className="ml-1 text-orange-600 dark:text-orange-400">
                    · Требуют внимания: {needsReviewCount}
                  </span>
                )}
              </span>
            ) : (
              <span>Строк: {stmt.total_lines ?? 0} (распознано {stmt.parsed_lines ?? 0}, ошибок {stmt.failed_lines ?? 0})</span>
            )}
          </div>
        </div>
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground text-lg leading-none">×</button>
      </div>

      {stmt.parse_error && (
        <div className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {stmt.parse_error}
        </div>
      )}

      {(canConfirm || canCancel) && (
        <div className="mt-4 flex gap-2">
          {canConfirm && (
            <button
              onClick={handleConfirm}
              disabled={confirm.isPending}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {confirm.isPending ? 'Запуск...' : 'Импортировать'}
            </button>
          )}
          {canCancel && (
            <button
              onClick={handleCancel}
              disabled={cancel.isPending}
              className="rounded-md border border-destructive/40 px-4 py-2 text-sm font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
            >
              Отменить
            </button>
          )}
        </div>
      )}

      {err && <p className="mt-2 text-xs text-destructive">{err}</p>}

      {isImporting && <ImportingBanner stmt={stmt} />}

      {!isImporting && stmt.status !== 'parse_failed' && (
        <div className="mt-5 border-t border-border pt-4">
          <LinesTable
            stmtId={stmt.id}
            defaultFilter={isImported && needsReviewCount > 0 ? 'review' : 'all'}
          />
        </div>
      )}
    </div>
  )
}

// ── Statements list ───────────────────────────────────────────────────────────

function StatementsList({ onSelect }: { onSelect: (s: BankStatement) => void }) {
  const { data, isLoading, error } = useBankStatements()

  if (isLoading) return <p className="text-sm text-muted-foreground">Загрузка...</p>
  if (error)    return <p className="text-sm text-destructive">Ошибка загрузки списка.</p>
  if (!data?.items?.length) return <p className="text-sm text-muted-foreground">Нет загруженных выписок.</p>

  return (
    <div className="space-y-2">
      {data.items.map((stmt: BankStatement) => {
        const isImported = stmt.status === 'imported'
        const needsReview = isImported
          ? (stmt.total_lines ?? 0) - (stmt.matched_lines ?? 0) - (stmt.failed_lines ?? 0)
          : 0
        return (
          <div
            key={stmt.id}
            className="flex cursor-pointer items-start justify-between gap-4 rounded-lg border border-border bg-card p-4 hover:bg-accent/50 transition-colors"
            onClick={() => onSelect(stmt)}
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm truncate">{stmt.file_name}</span>
                <StatusBadge stmt={stmt} />
              </div>
              <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                {stmt.period_from && <span>{stmt.period_from} — {stmt.period_to}</span>}
                {stmt.account_number && <span>{stmt.account_number}</span>}
                <span>{fmtDate(stmt.created_at)}</span>
              </div>
            </div>
            <div className="shrink-0 text-right text-xs">
              {isImported && stmt.matched_lines !== null && stmt.matched_lines !== undefined ? (
                <>
                  <div className="text-muted-foreground">{stmt.matched_lines} / {stmt.total_lines ?? 0}</div>
                  {needsReview > 0
                    ? <div className="text-orange-600 dark:text-orange-400">{needsReview} не сопост.</div>
                    : <div className="text-green-600 dark:text-green-400">все сопост.</div>
                  }
                </>
              ) : (
                <div className="text-muted-foreground">
                  <div>{stmt.parsed_lines ?? 0} / {stmt.total_lines ?? 0}</div>
                  <div>строк</div>
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function PaymentsPage() {
  const [selected, setSelected] = useState<BankStatement | null>(null)
  const { data, refetch } = useBankStatements()

  const handleUploaded = (id: string) => {
    refetch().then(() => {
      const stmt = data?.items?.find((s) => s.id === id)
      if (stmt) setSelected(stmt)
    })
    refetch()
  }

  const handleSelect = (stmt: BankStatement) => setSelected(stmt)

  // Sync selected with latest data after mutations
  const latestSelected = selected
    ? (data?.items?.find((s) => s.id === selected.id) ?? selected)
    : null

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-xl font-semibold">Выписки</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Импорт банковских выписок в формате КЛ→1С
        </p>
      </div>

      <div className="space-y-6">
        <UploadCard onUploaded={handleUploaded} />

        {latestSelected && (
          <StatementPanel
            stmt={latestSelected}
            onClose={() => setSelected(null)}
          />
        )}

        <Card title="История загрузок">
          <StatementsList onSelect={handleSelect} />
        </Card>
      </div>
    </div>
  )
}
