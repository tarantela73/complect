'use client'

import { use } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useCan } from '@/shared/auth/hooks'
import { useContragent, useDeleteContragent, CONTRAGENT_TYPE_LABELS } from '@/shared/api/contragents'
import { Button } from '@/shared/ui/button'

export default function ContragentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const router = useRouter()
  const canEdit = useCan('contragents.write')
  const canDelete = useCan('contragents.delete')

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
