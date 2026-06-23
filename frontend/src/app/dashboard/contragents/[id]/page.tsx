'use client'

import { use, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useCan } from '@/shared/auth/hooks'
import { useAuthStore } from '@/shared/auth/store'
import { downloadPdf } from '@/shared/api/reports'
import {
  useContragent,
  useDeleteContragent,
  useContragentAttachments,
  useAttachContragentFile,
  useDetachContragentFile,
  useContragentTimeline,
  CONTRAGENT_TYPE_LABELS,
  type ContragentAttachment,
  type ContragentTimelineEntry,
} from '@/shared/api/contragents'
import { useContracts, CONTRACT_STATUS_LABELS, type Contract } from '@/shared/api/contracts'
import { useFiles, FILE_STATUS_LABELS, FILE_STATUS_COLORS, formatFileSize } from '@/shared/api/files'
import { Button } from '@/shared/ui/button'
import { cn } from '@/shared/ui/utils'

type Tab = 'overview' | 'contracts' | 'files' | 'history'

const TAB_LABELS: Record<Tab, string> = {
  overview: 'Обзор',
  contracts: 'Договоры',
  files: 'Файлы',
  history: 'История',
}

function Row({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex px-4 py-3 gap-4">
      <dt className="w-40 shrink-0 text-sm font-medium text-muted-foreground">{label}</dt>
      <dd className={`text-sm flex-1 ${mono ? 'font-mono text-xs' : ''}`}>{value}</dd>
    </div>
  )
}

function ContractRow({ contract }: { contract: Contract }) {
  return (
    <tr className="border-t border-border hover:bg-muted/30">
      <td className="px-4 py-3 font-mono text-xs">{contract.contract_number}</td>
      <td className="px-4 py-3 text-sm">{contract.title}</td>
      <td className="px-4 py-3">
        <span className="rounded-full bg-muted px-2 py-0.5 text-xs">
          {CONTRACT_STATUS_LABELS[contract.status] ?? contract.status}
        </span>
      </td>
      <td className="px-4 py-3">
        <Link href={`/dashboard/contracts/${contract.id}`} className="text-xs text-primary hover:underline">
          Открыть →
        </Link>
      </td>
    </tr>
  )
}

function ContractsTab({ contragentId }: { contragentId: string }) {
  const { data, isLoading } = useContracts({ contragent_id: contragentId, limit: 50 })
  const contracts = data?.results ?? []

  if (isLoading) return <div className="py-8 text-center text-sm text-muted-foreground">Загрузка...</div>
  if (contracts.length === 0) return <div className="py-8 text-center text-sm text-muted-foreground">Договоров нет</div>

  return (
    <div className="rounded-md border border-border overflow-x-auto mt-4">
      <table className="w-full text-sm">
        <thead className="bg-muted/50">
          <tr>
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Номер</th>
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Наименование</th>
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Статус</th>
            <th className="px-4 py-2.5" />
          </tr>
        </thead>
        <tbody>{contracts.map((c) => <ContractRow key={c.id} contract={c} />)}</tbody>
      </table>
    </div>
  )
}

function FilesTab({ contragentId, canWrite }: { contragentId: string; canWrite: boolean }) {
  const { data: attachments, isLoading } = useContragentAttachments(contragentId)
  const { data: files } = useFiles({ status: 'AVAILABLE', limit: 100 })
  const attachMutation = useAttachContragentFile(contragentId)
  const detachMutation = useDetachContragentFile(contragentId)
  const [showPicker, setShowPicker] = useState(false)
  const [attachError, setAttachError] = useState('')

  const attachedIds = new Set((attachments?.results ?? []).map((a) => a.file_id))
  const available = (files?.results ?? []).filter((f) => !attachedIds.has(f.id))

  const handleAttach = (fileId: string) => {
    setAttachError('')
    attachMutation.mutate(fileId, {
      onSuccess: () => setShowPicker(false),
      onError: (e) => setAttachError(e.message),
    })
  }

  const handleDetach = (fileId: string, name: string) => {
    if (!confirm(`Открепить файл «${name}»?`)) return
    detachMutation.mutate(fileId, { onError: (e) => alert(e.message) })
  }

  return (
    <div className="mt-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm font-medium">Файлы</span>
        {canWrite && (
          <Button variant="outline" size="sm" onClick={() => { setShowPicker((v) => !v); setAttachError('') }}>
            {showPicker ? 'Отмена' : 'Прикрепить файл'}
          </Button>
        )}
      </div>

      {showPicker && (
        <div className="mb-3 rounded-md border border-border bg-muted/30 p-3">
          {attachError && <p className="mb-2 text-xs text-destructive">{attachError}</p>}
          {available.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              Нет доступных файлов.{' '}
              <Link href="/dashboard/files" className="underline">Загрузить →</Link>
            </p>
          ) : (
            <ul className="space-y-1 max-h-48 overflow-y-auto">
              {available.map((f) => (
                <li key={f.id} className="flex items-center gap-2 text-sm">
                  <span className="flex-1 truncate">{f.original_name}</span>
                  <span className="text-xs text-muted-foreground">{formatFileSize(f.size_bytes)}</span>
                  <Button size="sm" variant="outline" loading={attachMutation.isPending} onClick={() => handleAttach(f.id)}>
                    Прикрепить
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {isLoading ? (
        <p className="text-xs text-muted-foreground">Загрузка...</p>
      ) : (attachments?.results.length ?? 0) === 0 ? (
        <p className="text-xs text-muted-foreground">Файлов нет</p>
      ) : (
        <div className="rounded-md border border-border overflow-hidden">
          <table className="w-full text-sm">
            <tbody>
              {(attachments?.results ?? []).map((a: ContragentAttachment) => (
                <tr key={a.file_id} className="border-b border-border last:border-0 hover:bg-muted/20">
                  <td className="px-3 py-2 truncate max-w-xs">
                    {a.download_url ? (
                      <a href={a.download_url} target="_blank" rel="noreferrer" className="hover:underline">
                        {a.name}
                      </a>
                    ) : (
                      <span className="text-muted-foreground">{a.name}</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">{formatFileSize(a.size_bytes)}</td>
                  <td className="px-3 py-2">
                    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${FILE_STATUS_COLORS[a.status] ?? 'bg-muted text-muted-foreground'}`}>
                      {FILE_STATUS_LABELS[a.status] ?? a.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    {a.download_url && (
                      <a href={a.download_url} target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline mr-3">
                        Скачать
                      </a>
                    )}
                    {canWrite && (
                      <button onClick={() => handleDetach(a.file_id, a.name)} className="text-xs text-destructive hover:underline">
                        Открепить
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

const TIMELINE_COLORS: Record<string, string> = {
  'contragents.ContragentCreated': 'bg-blue-500',
  'contragents.ContragentUpdated': 'bg-yellow-500',
  'contragents.ContragentDeleted': 'bg-red-500',
  'contragents.ContragentFileAttached': 'bg-green-500',
  'contragents.ContragentFileDetached': 'bg-orange-400',
  'contragents.PersonProfileCreated': 'bg-purple-500',
}

function HistoryTab({ contragentId }: { contragentId: string }) {
  const { data, isLoading } = useContragentTimeline(contragentId)

  if (isLoading) return <div className="py-8 text-center text-sm text-muted-foreground">Загрузка...</div>
  if (!data || data.count === 0) return <div className="py-8 text-center text-sm text-muted-foreground">История пуста</div>

  return (
    <ol className="relative border-l border-border ml-3 mt-4">
      {data.results.map((entry: ContragentTimelineEntry) => (
        <li key={entry.event_id} className="mb-5 ml-5">
          <span
            className={`absolute -left-1.5 mt-1 h-3 w-3 rounded-full border border-background ${TIMELINE_COLORS[entry.event_type] ?? 'bg-muted-foreground'}`}
          />
          <p className="text-sm font-medium">{entry.label}</p>
          <p className="text-xs text-muted-foreground">
            {new Date(entry.occurred_at).toLocaleString('ru-RU')}
          </p>
        </li>
      ))}
    </ol>
  )
}

export default function ContragentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const router = useRouter()
  const canEdit = useCan('contragents.write')
  const canDelete = useCan('contragents.delete')
  const [activeTab, setActiveTab] = useState<Tab>('overview')
  const accessToken = useAuthStore((s) => s.accessToken)
  const [pdfLoading, setPdfLoading] = useState(false)

  const { data, isLoading, isError, error } = useContragent(id)
  const deleteMutation = useDeleteContragent()

  if (isLoading) return <div className="p-6 text-sm text-muted-foreground">Загрузка...</div>

  if (isError) {
    const is404 = error.message === 'NOT_FOUND'
    return (
      <div className="p-6">
        <p className="text-sm text-destructive">{is404 ? 'Контрагент не найден' : 'Ошибка загрузки данных'}</p>
        <Button variant="outline" size="sm" className="mt-4" onClick={() => router.back()}>← Назад</Button>
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

  const handlePdf = async () => {
    setPdfLoading(true)
    try {
      await downloadPdf(`/api/v1/reports/contragents/${id}/pdf/`, `contragent_${id}.pdf`, accessToken)
    } catch {
      alert('Ошибка при формировании PDF')
    } finally {
      setPdfLoading(false)
    }
  }

  return (
    <div className="p-6 max-w-2xl">
      <div className="mb-6 flex items-center gap-4">
        <Link href="/dashboard/contragents" className="text-sm text-muted-foreground hover:text-foreground">
          ← Контрагенты
        </Link>
        <h1 className="text-xl font-semibold flex-1 truncate">{data.name}</h1>
        <div className="flex gap-2 shrink-0">
          <Button variant="outline" size="sm" loading={pdfLoading} onClick={handlePdf}>PDF</Button>
          {canEdit && (
            <Link href={`/dashboard/contragents/${id}/edit`}>
              <Button variant="outline" size="sm">Изменить</Button>
            </Link>
          )}
          {canDelete && (
            <Button variant="destructive" size="sm" loading={deleteMutation.isPending} onClick={handleDelete}>
              Удалить
            </Button>
          )}
        </div>
      </div>

      <div className="flex gap-1 border-b border-border mb-4">
        {(['overview', 'contracts', 'files', 'history'] as Tab[]).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={cn(
              'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
              activeTab === tab
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </div>

      {activeTab === 'overview' && (
        <dl className="divide-y divide-border rounded-md border border-border">
          <Row label="ИНН" value={data.inn} />
          <Row label="Тип" value={CONTRAGENT_TYPE_LABELS[data.contragent_type] ?? data.contragent_type} />
          <Row
            label="Дата создания"
            value={data.created_at ? new Date(data.created_at).toLocaleString('ru-RU') : '—'}
          />
          <Row label="ID" value={data.id} mono />
        </dl>
      )}

      {activeTab === 'contracts' && <ContractsTab contragentId={id} />}
      {activeTab === 'files' && <FilesTab contragentId={id} canWrite={canEdit} />}
      {activeTab === 'history' && <HistoryTab contragentId={id} />}
    </div>
  )
}
