'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useCan } from '@/shared/auth/hooks'
import {
  useObjects,
  useCreateObject,
  OBJECT_STATUS_LABELS,
  RESOURCE_TYPE_LABELS,
  type SupplyObject,
  type ObjectCreateRequest,
} from '@/shared/api/objects'
import { Button } from '@/shared/ui/button'

const RESOURCE_TYPES = Object.keys(RESOURCE_TYPE_LABELS)
const OBJECT_STATUSES = Object.keys(OBJECT_STATUS_LABELS)

const STATUS_COLORS: Record<string, string> = {
  NEW: 'bg-muted text-muted-foreground',
  IN_PROGRESS: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  PAUSED: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  COMPLETED: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  CANCELLED: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[status] ?? 'bg-muted text-muted-foreground'}`}>
      {OBJECT_STATUS_LABELS[status] ?? status}
    </span>
  )
}

function ObjectRow({ obj }: { obj: SupplyObject }) {
  return (
    <tr className="border-b border-border hover:bg-muted/30 transition-colors">
      <td className="px-4 py-3 text-sm">
        <Link
          href={`/dashboard/objects/${obj.id}`}
          className="font-medium hover:underline text-foreground"
        >
          {obj.name}
        </Link>
      </td>
      <td className="px-4 py-3 text-sm text-muted-foreground">{obj.address || '—'}</td>
      <td className="px-4 py-3 text-sm">{RESOURCE_TYPE_LABELS[obj.resource_type] ?? obj.resource_type}</td>
      <td className="px-4 py-3">
        <StatusBadge status={obj.status} />
      </td>
      <td className="px-4 py-3 text-sm text-muted-foreground">
        {obj.manager ? obj.manager.full_name : '—'}
      </td>
      <td className="px-4 py-3 text-sm text-right">
        <Link href={`/dashboard/objects/${obj.id}`}>
          <Button variant="ghost" size="sm">→</Button>
        </Link>
      </td>
    </tr>
  )
}

function CreateObjectModal({
  onClose,
}: {
  onClose: () => void
}) {
  const create = useCreateObject()
  const [form, setForm] = useState({
    name: '',
    address: '',
    object_type: 'BUILDING',
    resource_type: 'GAS',
  })

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await create.mutateAsync(form as ObjectCreateRequest)
      onClose()
    } catch {
      // error shown inline
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-md rounded-lg bg-background p-6 shadow-xl">
        <h2 className="mb-4 text-base font-semibold">Новый объект</h2>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Название *</label>
            <input
              required
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Адрес</label>
            <input
              value={form.address}
              onChange={(e) => setForm((f) => ({ ...f, address: e.target.value }))}
              className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Тип объекта *</label>
            <select
              value={form.object_type}
              onChange={(e) => setForm((f) => ({ ...f, object_type: e.target.value }))}
              className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm"
            >
              {['BUILDING', 'GRS', 'PIPELINE', 'SUBSTATION', 'RESERVOIR', 'HEATING_POINT', 'OTHER'].map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Тип ресурса *</label>
            <select
              value={form.resource_type}
              onChange={(e) => setForm((f) => ({ ...f, resource_type: e.target.value }))}
              className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm"
            >
              {RESOURCE_TYPES.map((r) => (
                <option key={r} value={r}>{RESOURCE_TYPE_LABELS[r]}</option>
              ))}
            </select>
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

export default function ObjectsPage() {
  const canCreate = useCan('objects.create')

  const [statusFilter, setStatusFilter] = useState('')
  const [resourceFilter, setResourceFilter] = useState('')
  const [cursors, setCursors] = useState<string[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const currentCursor = cursors.at(-1)

  const { data, isLoading, isError } = useObjects({
    cursor: currentCursor,
    limit: 20,
    status: statusFilter || undefined,
    resource_type: resourceFilter || undefined,
  })

  const handleFilterChange = () => setCursors([])

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Объекты</h1>
        {canCreate && (
          <Button size="sm" onClick={() => setShowCreate(true)}>Создать объект</Button>
        )}
      </div>

      <div className="mb-4 flex gap-3">
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); handleFilterChange() }}
          className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <option value="">Все статусы</option>
          {OBJECT_STATUSES.map((s) => (
            <option key={s} value={s}>{OBJECT_STATUS_LABELS[s]}</option>
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

      {isError && <p className="text-sm text-destructive">Ошибка загрузки объектов</p>}

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Загрузка...</p>
      ) : (
        <>
          <div className="rounded-md border border-border overflow-hidden">
            <table className="w-full">
              <thead className="bg-muted/50">
                <tr>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Название</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Адрес</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Тип ресурса</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Статус</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Менеджер</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {data?.results.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-sm text-muted-foreground">
                      Объектов нет
                    </td>
                  </tr>
                ) : (
                  data?.results.map((o) => <ObjectRow key={o.id} obj={o} />)
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

      {showCreate && <CreateObjectModal onClose={() => setShowCreate(false)} />}
    </div>
  )
}
