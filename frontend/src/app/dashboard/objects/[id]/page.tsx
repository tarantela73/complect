'use client'

import { use, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useCan } from '@/shared/auth/hooks'
import {
  useObject,
  useLinkObjectContragent,
  OBJECT_STATUS_LABELS,
  RESOURCE_TYPE_LABELS,
} from '@/shared/api/objects'
import { useContragent } from '@/shared/api/contragents'
import { apiClient } from '@/shared/api/client'
import { Button } from '@/shared/ui/button'
import { cn } from '@/shared/ui/utils'
import { AsyncSelect, type AsyncSelectOption } from '@/shared/ui/AsyncSelect'
import { StagesChecklist } from './components/StagesChecklist'
import { ContractsTab } from './components/ContractsTab'
import { LocationSection } from './components/LocationSection'
import { PublicSiteSection } from './components/PublicSiteSection'
import type { components } from '@/shared/api/generated/schema'

type ContragentResponse = components['schemas']['ContragentResponse']

const OBJECT_TYPE_LABELS: Record<string, string> = {
  BUILDING: 'Здание',
  GRS: 'ГРС',
  PIPELINE: 'Трубопровод',
  SUBSTATION: 'Подстанция',
  RESERVOIR: 'Резервуар',
  HEATING_POINT: 'Тепловой пункт',
  OTHER: 'Прочее',
}

const STATUS_COLORS: Record<string, string> = {
  NEW: 'bg-muted text-muted-foreground',
  IN_PROGRESS: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  PAUSED: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  COMPLETED: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  CANCELLED: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
}

type Tab = 'main' | 'stages' | 'contracts'

const TAB_LABELS: Record<Tab, string> = {
  main: 'Основное',
  stages: 'Этапы',
  contracts: 'Договоры',
}

function Row({ label, value, mono }: { label: string; value?: string | null; mono?: boolean }) {
  return (
    <div className="flex px-4 py-3 gap-4">
      <dt className="w-40 shrink-0 text-sm text-muted-foreground">{label}</dt>
      <dd className={cn('text-sm', mono && 'font-mono')}>{value || '—'}</dd>
    </div>
  )
}

function ContragentSection({
  objectId,
  contragentId,
  canEdit,
}: {
  objectId: string
  contragentId: string | null | undefined
  canEdit: boolean
}) {
  const { data: contragent, isLoading } = useContragent(contragentId ?? '')
  const linkMutation = useLinkObjectContragent(objectId)
  const [showPicker, setShowPicker] = useState(false)
  const [selected, setSelected] = useState<AsyncSelectOption | null>(null)

  const searchContragents = useCallback(async (q: string): Promise<AsyncSelectOption[]> => {
    const { data, error } = await apiClient.GET('/api/v1/contragents/', {
      params: { query: { q, limit: 20 } },
    })
    if (error || !data) return []
    return (data as { results: ContragentResponse[] }).results.map((c) => ({
      value: c.id,
      label: `${c.name} (${c.inn})`,
    }))
  }, [])

  const handleLink = async () => {
    if (!selected) return
    await linkMutation.mutateAsync(selected.value)
    setShowPicker(false)
    setSelected(null)
  }

  const handleUnlink = async () => {
    if (!confirm('Отвязать контрагента от объекта?')) return
    await linkMutation.mutateAsync(null)
  }

  return (
    <div className="mt-6">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-medium">Контрагент</h2>
        {canEdit && !showPicker && (
          <Button variant="outline" size="sm" onClick={() => setShowPicker(true)}>
            {contragentId ? 'Изменить' : 'Привязать'}
          </Button>
        )}
        {canEdit && showPicker && (
          <Button variant="ghost" size="sm" onClick={() => { setShowPicker(false); setSelected(null) }}>
            Отмена
          </Button>
        )}
      </div>

      {showPicker && (
        <div className="mb-3 space-y-2">
          <AsyncSelect
            placeholder="Поиск по названию или ИНН..."
            onSearch={searchContragents}
            onSelect={setSelected}
            value={selected}
          />
          <Button size="sm" disabled={!selected} loading={linkMutation.isPending} onClick={handleLink}>
            Привязать
          </Button>
        </div>
      )}

      {!showPicker && contragentId ? (
        isLoading ? (
          <p className="text-sm text-muted-foreground">Загрузка...</p>
        ) : contragent ? (
          <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
            <Link
              href={`/dashboard/contragents/${contragent.id}`}
              className="text-sm font-medium hover:underline"
            >
              {contragent.name}
              <span className="ml-2 text-xs text-muted-foreground font-mono">{contragent.inn}</span>
            </Link>
            {canEdit && (
              <button
                onClick={handleUnlink}
                disabled={linkMutation.isPending}
                className="text-xs text-destructive hover:underline ml-4"
              >
                Отвязать
              </button>
            )}
          </div>
        ) : null
      ) : (
        !showPicker && <p className="text-sm text-muted-foreground">Контрагент не привязан</p>
      )}
    </div>
  )
}

export default function ObjectDetailPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const router = useRouter()
  const canEdit = useCan('objects.write')
  const [activeTab, setActiveTab] = useState<Tab>('main')

  const { data, isLoading, isError, error } = useObject(id)

  if (isLoading) {
    return (
      <div className="p-6">
        <div className="text-sm text-muted-foreground">Загрузка...</div>
      </div>
    )
  }

  if (isError) {
    const is404 = (error as Error).message === 'NOT_FOUND'
    return (
      <div className="p-6">
        <p className="text-sm text-destructive">
          {is404 ? 'Объект не найден' : 'Ошибка загрузки данных'}
        </p>
        <Button variant="outline" size="sm" className="mt-4" onClick={() => router.back()}>
          ← Назад
        </Button>
      </div>
    )
  }

  if (!data) return null

  return (
    <div className="p-6 max-w-3xl">
      <div className="mb-6 flex items-start gap-4">
        <Link
          href="/dashboard/objects"
          className="text-sm text-muted-foreground hover:text-foreground mt-1"
        >
          ← Объекты
        </Link>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-xl font-semibold truncate">{data.name}</h1>
            <span
              className={cn(
                'inline-flex rounded-full px-2 py-0.5 text-xs font-medium',
                STATUS_COLORS[data.status] ?? 'bg-muted text-muted-foreground'
              )}
            >
              {OBJECT_STATUS_LABELS[data.status] ?? data.status}
            </span>
          </div>
          {data.address && (
            <p className="text-sm text-muted-foreground mt-0.5">{data.address}</p>
          )}
        </div>
        {canEdit && (
          <Button variant="outline" size="sm" className="shrink-0">
            Редактировать
          </Button>
        )}
      </div>

      <div className="flex gap-1 border-b border-border mb-4">
        {(['main', 'stages', 'contracts'] as Tab[]).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={cn(
              'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
              activeTab === tab
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            )}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </div>

      {activeTab === 'main' && (
        <>
        <dl className="divide-y divide-border rounded-md border border-border">
          <Row
            label="Тип объекта"
            value={OBJECT_TYPE_LABELS[data.object_type] ?? data.object_type}
          />
          <Row
            label="Тип ресурса"
            value={RESOURCE_TYPE_LABELS[data.resource_type] ?? data.resource_type}
          />
          <Row label="Описание" value={data.description || undefined} />
          <Row label="Команда" value={data.team_name || undefined} />
          <Row label="Менеджер" value={data.manager?.full_name} />
          <Row label="Прораб" value={data.foreman?.full_name} />
          <Row
            label="Плановые сроки"
            value={
              data.planned_start_date && data.planned_end_date
                ? `${fmtDate(data.planned_start_date)} — ${fmtDate(data.planned_end_date)}`
                : data.planned_start_date
                  ? `с ${fmtDate(data.planned_start_date)}`
                  : undefined
            }
          />
          <Row
            label="Фактические сроки"
            value={
              data.actual_start_date && data.actual_end_date
                ? `${fmtDate(data.actual_start_date)} — ${fmtDate(data.actual_end_date)}`
                : data.actual_start_date
                  ? `с ${fmtDate(data.actual_start_date)}`
                  : undefined
            }
          />
          <Row label="ID" value={data.id} mono />
        </dl>
        <ContragentSection objectId={id} contragentId={data.contragent_id} canEdit={canEdit} />
        <LocationSection
          objectId={id}
          longitude={data.longitude}
          latitude={data.latitude}
        />
        <PublicSiteSection
          objectId={id}
          isPublic={data.is_public ?? false}
          slug={data.slug}
          coverImageKey={data.cover_image_key}
          canEdit={canEdit}
        />
        </>
      )}

      {activeTab === 'stages' && (
        <StagesChecklist stages={data.stages ?? []} objectId={id} />
      )}

      {activeTab === 'contracts' && <ContractsTab objectId={id} />}
    </div>
  )
}

function fmtDate(d: string | null | undefined): string {
  if (!d) return '—'
  return new Date(d).toLocaleDateString('ru-RU')
}
