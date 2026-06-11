'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useCan } from '@/shared/auth/hooks'
import {
  useRequests,
  useCreateRequest,
  REQUEST_STATUS_LABELS,
  REQUEST_STATUS_COLORS,
  REQUEST_RESOURCE_LABELS,
  REQUEST_SOURCE_LABELS,
  type RequestItem,
  type RequestCreateRequest,
} from '@/shared/api/requests'
import { Button } from '@/shared/ui/button'

const ALL_STATUSES = Object.keys(REQUEST_STATUS_LABELS)
const ALL_RESOURCES = Object.keys(REQUEST_RESOURCE_LABELS)

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${REQUEST_STATUS_COLORS[status] ?? 'bg-muted text-muted-foreground'}`}
    >
      {REQUEST_STATUS_LABELS[status] ?? status}
    </span>
  )
}

function RequestRow({ req }: { req: RequestItem }) {
  return (
    <tr className="border-b border-border hover:bg-muted/30 transition-colors">
      <td className="px-4 py-3 text-sm">
        <Link
          href={`/dashboard/requests/${req.id}`}
          className="font-medium hover:underline text-foreground"
        >
          {req.client_name}
        </Link>
      </td>
      <td className="px-4 py-3 text-sm text-muted-foreground">{req.client_phone}</td>
      <td className="px-4 py-3 text-sm text-muted-foreground truncate max-w-[200px]">
        {req.address}
      </td>
      <td className="px-4 py-3 text-sm">
        {REQUEST_RESOURCE_LABELS[req.resource_type] ?? req.resource_type}
      </td>
      <td className="px-4 py-3">
        <StatusBadge status={req.status} />
      </td>
      <td className="px-4 py-3 text-sm text-muted-foreground">
        {REQUEST_SOURCE_LABELS[req.source] ?? req.source}
      </td>
      <td className="px-4 py-3 text-right">
        <Link href={`/dashboard/requests/${req.id}`}>
          <Button variant="ghost" size="sm">→</Button>
        </Link>
      </td>
    </tr>
  )
}

function CreateRequestModal({ onClose }: { onClose: () => void }) {
  const create = useCreateRequest()
  const [form, setForm] = useState({
    client_name: '',
    client_phone: '',
    client_email: '',
    address: '',
    resource_type: 'GAS',
    source: 'MANUAL',
    comment: '',
  })

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await create.mutateAsync(form as RequestCreateRequest)
      onClose()
    } catch {
      // error shown inline
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-md rounded-lg bg-background p-6 shadow-xl">
        <h2 className="mb-4 text-base font-semibold">Новая заявка</h2>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">ФИО клиента *</label>
            <input
              required
              value={form.client_name}
              onChange={(e) => setForm((f) => ({ ...f, client_name: e.target.value }))}
              className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Телефон *</label>
            <input
              required
              value={form.client_phone}
              onChange={(e) => setForm((f) => ({ ...f, client_phone: e.target.value }))}
              className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Адрес *</label>
            <input
              required
              value={form.address}
              onChange={(e) => setForm((f) => ({ ...f, address: e.target.value }))}
              className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Тип ресурса *</label>
            <select
              value={form.resource_type}
              onChange={(e) => setForm((f) => ({ ...f, resource_type: e.target.value }))}
              className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm"
            >
              {ALL_RESOURCES.map((r) => (
                <option key={r} value={r}>{REQUEST_RESOURCE_LABELS[r]}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Источник</label>
            <select
              value={form.source}
              onChange={(e) => setForm((f) => ({ ...f, source: e.target.value }))}
              className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm"
            >
              {Object.entries(REQUEST_SOURCE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Комментарий</label>
            <textarea
              value={form.comment}
              onChange={(e) => setForm((f) => ({ ...f, comment: e.target.value }))}
              rows={2}
              className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>

          {create.isError && (
            <p className="text-xs text-destructive">{(create.error as Error).message}</p>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>Отмена</Button>
            <Button type="submit" size="sm" disabled={create.isPending}>
              {create.isPending ? 'Создание...' : 'Создать'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function RequestsPage() {
  const canCreate = useCan('requests.write')

  const [statusFilter, setStatusFilter] = useState('')
  const [resourceFilter, setResourceFilter] = useState('')
  const [cursors, setCursors] = useState<string[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const currentCursor = cursors.at(-1)

  const { data, isLoading, isError } = useRequests({
    cursor: currentCursor,
    limit: 20,
    status: statusFilter || undefined,
    resource_type: resourceFilter || undefined,
  })

  const handleFilterChange = () => setCursors([])

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Заявки</h1>
        {canCreate && (
          <Button size="sm" onClick={() => setShowCreate(true)}>Создать заявку</Button>
        )}
      </div>

      <div className="mb-4 flex gap-3">
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); handleFilterChange() }}
          className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <option value="">Все статусы</option>
          {ALL_STATUSES.map((s) => (
            <option key={s} value={s}>{REQUEST_STATUS_LABELS[s]}</option>
          ))}
        </select>

        <select
          value={resourceFilter}
          onChange={(e) => { setResourceFilter(e.target.value); handleFilterChange() }}
          className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <option value="">Все типы ресурсов</option>
          {ALL_RESOURCES.map((r) => (
            <option key={r} value={r}>{REQUEST_RESOURCE_LABELS[r]}</option>
          ))}
        </select>

        {(statusFilter || resourceFilter) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => { setStatusFilter(''); setResourceFilter(''); handleFilterChange() }}
          >
            Сбросить
          </Button>
        )}
      </div>

      {isError && <p className="text-sm text-destructive">Ошибка загрузки заявок</p>}

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Загрузка...</p>
      ) : (
        <>
          <div className="rounded-md border border-border overflow-hidden">
            <table className="w-full">
              <thead className="bg-muted/50">
                <tr>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Клиент</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Телефон</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Адрес</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Тип ресурса</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Статус</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Источник</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {data?.results.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-sm text-muted-foreground">
                      Заявок нет
                    </td>
                  </tr>
                ) : (
                  data?.results.map((r) => <RequestRow key={r.id} req={r} />)
                )}
              </tbody>
            </table>
          </div>

          <div className="mt-4 flex gap-2">
            {cursors.length > 0 && (
              <Button variant="outline" size="sm" onClick={() => setCursors((prev) => prev.slice(0, -1))}>
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

      {showCreate && <CreateRequestModal onClose={() => setShowCreate(false)} />}
    </div>
  )
}
