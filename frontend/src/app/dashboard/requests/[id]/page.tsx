'use client'

import { use } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useCan } from '@/shared/auth/hooks'
import {
  useRequest,
  useAssignRequest,
  useProcessRequest,
  useConvertRequest,
  useCloseRequest,
  REQUEST_STATUS_LABELS,
  REQUEST_STATUS_COLORS,
  REQUEST_SOURCE_LABELS,
  REQUEST_RESOURCE_LABELS,
} from '@/shared/api/requests'
import { Button } from '@/shared/ui/button'
import { cn } from '@/shared/ui/utils'

export default function RequestDetailPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const router = useRouter()
  const canWrite = useCan('requests.write')

  const { data, isLoading, isError, error } = useRequest(id)
  const assign = useAssignRequest(id)
  const process_ = useProcessRequest(id)
  const convert = useConvertRequest(id)
  const close = useCloseRequest(id)

  if (isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Загрузка...</div>
  }

  if (isError) {
    const is404 = (error as Error).message === 'NOT_FOUND'
    return (
      <div className="p-6">
        <p className="text-sm text-destructive">
          {is404 ? 'Заявка не найдена' : 'Ошибка загрузки'}
        </p>
        <Button variant="outline" size="sm" className="mt-4" onClick={() => router.back()}>
          ← Назад
        </Button>
      </div>
    )
  }

  if (!data) return null

  const handleConvert = async () => {
    if (!confirm('Конвертировать заявку в объект?')) return
    try {
      const result = await convert.mutateAsync()
      router.push(`/dashboard/objects/${result.object_id}`)
    } catch (e) {
      alert((e as Error).message)
    }
  }

  const handleClose = async () => {
    if (!confirm('Закрыть заявку?')) return
    try {
      await close.mutateAsync()
    } catch (e) {
      alert((e as Error).message)
    }
  }

  const canAssign = canWrite && data.status === 'NEW'
  const canProcess = canWrite && data.status === 'ASSIGNED'
  const canConvert = canWrite && data.status === 'PROCESSING'
  const canClose = canWrite && ['NEW', 'ASSIGNED', 'PROCESSING'].includes(data.status)

  return (
    <div className="p-6 max-w-2xl">
      <div className="mb-6 flex items-start gap-4">
        <Link
          href="/dashboard/requests"
          className="text-sm text-muted-foreground hover:text-foreground mt-1"
        >
          ← Заявки
        </Link>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-xl font-semibold">{data.client_name}</h1>
            <span
              className={cn(
                'inline-flex rounded-full px-2 py-0.5 text-xs font-medium',
                REQUEST_STATUS_COLORS[data.status] ?? 'bg-muted text-muted-foreground'
              )}
            >
              {REQUEST_STATUS_LABELS[data.status] ?? data.status}
            </span>
          </div>
          <p className="text-sm text-muted-foreground mt-0.5">{data.client_phone}</p>
        </div>
      </div>

      <dl className="divide-y divide-border rounded-md border border-border mb-6">
        <Row label="Адрес" value={data.address} />
        <Row label="Тип ресурса" value={REQUEST_RESOURCE_LABELS[data.resource_type] ?? data.resource_type} />
        <Row label="Источник" value={REQUEST_SOURCE_LABELS[data.source] ?? data.source} />
        <Row label="Email" value={data.client_email || undefined} />
        <Row label="Комментарий" value={data.comment || undefined} />
        {data.converted_object_id && (
          <div className="flex px-4 py-3 gap-4">
            <dt className="w-40 shrink-0 text-sm text-muted-foreground">Объект</dt>
            <dd className="text-sm">
              <Link
                href={`/dashboard/objects/${data.converted_object_id}`}
                className="text-primary hover:underline"
              >
                Открыть объект →
              </Link>
            </dd>
          </div>
        )}
        <Row label="ID" value={data.id} mono />
        <Row
          label="Создана"
          value={new Date(data.created_at).toLocaleString('ru-RU')}
        />
      </dl>

      {canWrite && (
        <div className="flex flex-wrap gap-2">
          {canAssign && (
            <Button
              variant="outline"
              size="sm"
              disabled={assign.isPending}
              onClick={() => {
                const manager_id = prompt('UUID менеджера:')
                if (!manager_id) return
                assign.mutate({ manager_id })
              }}
            >
              Назначить менеджера
            </Button>
          )}
          {canProcess && (
            <Button
              variant="outline"
              size="sm"
              disabled={process_.isPending}
              onClick={() => process_.mutate()}
            >
              Взять в обработку
            </Button>
          )}
          {canConvert && (
            <Button
              size="sm"
              disabled={convert.isPending}
              onClick={handleConvert}
            >
              {convert.isPending ? 'Конвертация...' : 'Конвертировать в объект'}
            </Button>
          )}
          {canClose && (
            <Button
              variant="destructive"
              size="sm"
              disabled={close.isPending}
              onClick={handleClose}
            >
              Закрыть
            </Button>
          )}
        </div>
      )}
    </div>
  )
}

function Row({ label, value, mono }: { label: string; value?: string | null; mono?: boolean }) {
  return (
    <div className="flex px-4 py-3 gap-4">
      <dt className="w-40 shrink-0 text-sm text-muted-foreground">{label}</dt>
      <dd className={cn('text-sm', mono && 'font-mono')}>{value || '—'}</dd>
    </div>
  )
}
