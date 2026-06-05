'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useCan } from '@/shared/auth/hooks'
import {
  useContragents,
  useDeleteContragent,
  CONTRAGENT_TYPE_LABELS,
  type Contragent,
} from '@/shared/api/contragents'
import { useDebounce } from '@/shared/ui/use-debounce'
import { Button } from '@/shared/ui/button'

function ContragentRow({
  item,
  canEdit,
  canDelete,
  onDelete,
}: {
  item: Contragent
  canEdit: boolean
  canDelete: boolean
  onDelete: (id: string) => void
}) {
  return (
    <tr className="border-b border-border hover:bg-muted/50 transition-colors">
      <td className="py-3 px-4">
        <Link
          href={`/dashboard/contragents/${item.id}`}
          className="font-medium text-primary hover:underline"
        >
          {item.name}
        </Link>
      </td>
      <td className="py-3 px-4 text-sm text-muted-foreground">{item.inn}</td>
      <td className="py-3 px-4 text-sm">
        {CONTRAGENT_TYPE_LABELS[item.contragent_type] ?? item.contragent_type}
      </td>
      <td className="py-3 px-4 text-sm text-muted-foreground">
        {item.created_at ? new Date(item.created_at).toLocaleDateString('ru-RU') : '—'}
      </td>
      <td className="py-3 px-4">
        <div className="flex gap-2">
          {canEdit && (
            <Link href={`/dashboard/contragents/${item.id}/edit`}>
              <Button variant="outline" size="sm">Изменить</Button>
            </Link>
          )}
          {canDelete && (
            <Button
              variant="destructive"
              size="sm"
              onClick={() => onDelete(item.id)}
            >
              Удалить
            </Button>
          )}
        </div>
      </td>
    </tr>
  )
}

export default function ContragentsPage() {
  const router = useRouter()
  const canCreate = useCan('contragents.create')
  const canEdit = useCan('contragents.write')
  const canDelete = useCan('contragents.delete')

  const [cursor, setCursor] = useState<string | undefined>()
  const [cursors, setCursors] = useState<string[]>([])
  const [typeFilter, setTypeFilter] = useState('')
  const [searchInput, setSearchInput] = useState('')

  const q = useDebounce(searchInput)

  const { data, isLoading, isError } = useContragents({
    cursor,
    limit: 20,
    contragent_type: typeFilter || undefined,
    q: q || undefined,
  })

  const deleteMutation = useDeleteContragent()

  const handleDelete = (id: string) => {
    if (!confirm('Удалить контрагента?')) return
    deleteMutation.mutate(id, {
      onError: (e) => alert(e.message),
    })
  }

  const handleNextPage = () => {
    if (!data?.next_cursor) return
    setCursors((prev) => [...prev, cursor ?? ''])
    setCursor(data.next_cursor)
  }

  const handlePrevPage = () => {
    const prev = [...cursors]
    const last = prev.pop()
    setCursors(prev)
    setCursor(last || undefined)
  }

  const handleTypeChange = (val: string) => {
    setTypeFilter(val)
    setCursor(undefined)
    setCursors([])
  }

  const handleSearchChange = (val: string) => {
    setSearchInput(val)
    setCursor(undefined)
    setCursors([])
  }

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Контрагенты</h1>
        {canCreate && (
          <Button onClick={() => router.push('/dashboard/contragents/create')}>
            Добавить
          </Button>
        )}
      </div>

      <div className="mb-4 flex gap-3">
        <input
          value={searchInput}
          onChange={(e) => handleSearchChange(e.target.value)}
          placeholder="Поиск по названию или ИНН..."
          className="rounded-md border border-input bg-background px-3 py-2 text-sm w-72 focus:outline-none focus:ring-2 focus:ring-ring"
        />

        <select
          value={typeFilter}
          onChange={(e) => handleTypeChange(e.target.value)}
          className="rounded-md border border-input bg-background px-3 py-2 text-sm"
        >
          <option value="">Все типы</option>
          <option value="LEGAL_ENTITY">Юридическое лицо</option>
          <option value="ENTREPRENEUR">Индивидуальный предприниматель</option>
          <option value="INDIVIDUAL">Физическое лицо</option>
        </select>

        {(searchInput || typeFilter) && (
          <Button
            variant="ghost"
            size="default"
            onClick={() => { setSearchInput(''); setTypeFilter(''); setCursor(undefined); setCursors([]) }}
          >
            Сбросить
          </Button>
        )}
      </div>

      {isLoading && (
        <div className="py-12 text-center text-sm text-muted-foreground">Загрузка...</div>
      )}

      {isError && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
          Ошибка загрузки данных
        </div>
      )}

      {!isLoading && !isError && data && (
        <>
          <div className="rounded-md border border-border overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-muted/50">
                <tr>
                  <th className="py-3 px-4 text-left font-medium">Наименование</th>
                  <th className="py-3 px-4 text-left font-medium">ИНН</th>
                  <th className="py-3 px-4 text-left font-medium">Тип</th>
                  <th className="py-3 px-4 text-left font-medium">Дата создания</th>
                  <th className="py-3 px-4 text-left font-medium">Действия</th>
                </tr>
              </thead>
              <tbody>
                {data.results.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="py-10 text-center text-muted-foreground">
                      {q ? `Ничего не найдено по запросу «${q}»` : 'Контрагенты не найдены'}
                    </td>
                  </tr>
                ) : (
                  data.results.map((item) => (
                    <ContragentRow
                      key={item.id}
                      item={item}
                      canEdit={canEdit}
                      canDelete={canDelete}
                      onDelete={handleDelete}
                    />
                  ))
                )}
              </tbody>
            </table>
          </div>

          <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
            <span>Показано: {data.results.length}</span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={cursors.length === 0}
                onClick={handlePrevPage}
              >
                ← Назад
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!data.next_cursor}
                onClick={handleNextPage}
              >
                Вперёд →
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
