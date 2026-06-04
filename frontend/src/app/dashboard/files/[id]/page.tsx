'use client'

import { use } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useCan } from '@/shared/auth/hooks'
import {
  useFile,
  useDeleteFile,
  useDownloadUrl,
  FILE_STATUS_LABELS,
  FILE_STATUS_COLORS,
  formatFileSize,
} from '@/shared/api/files'
import { Button } from '@/shared/ui/button'

function Row({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex px-4 py-3 gap-4">
      <dt className="w-44 shrink-0 text-sm font-medium text-muted-foreground">{label}</dt>
      <dd className={`text-sm flex-1 break-all ${mono ? 'font-mono text-xs' : ''}`}>{value}</dd>
    </div>
  )
}

export default function FileDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const router = useRouter()
  const canDelete = useCan('files.delete')
  const canDownload = useCan('files.download')

  const { data, isLoading, isError } = useFile(id)
  const deleteMutation = useDeleteFile()
  const downloadMutation = useDownloadUrl()

  if (isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Загрузка...</div>
  }

  if (isError || !data) {
    return (
      <div className="p-6">
        <p className="text-sm text-destructive">Файл не найден</p>
        <Button variant="outline" size="sm" className="mt-4" onClick={() => router.back()}>
          ← Назад
        </Button>
      </div>
    )
  }

  const isAvailable = data.status === 'AVAILABLE'
  const isDeleted = data.status === 'DELETED'
  const isTransient = ['PENDING_UPLOAD', 'UPLOADED', 'SCANNING'].includes(data.status)
  const isQuarantined = data.status === 'QUARANTINED'

  const handleDelete = () => {
    if (!confirm(`Удалить файл «${data.original_name}»?`)) return
    deleteMutation.mutate(id, {
      onSuccess: () => router.push('/dashboard/files'),
      onError: (e) => alert(e.message),
    })
  }

  const handleDownload = () => {
    downloadMutation.mutate(id, {
      onSuccess: (result) => window.open(result.download_url, '_blank'),
      onError: (e) => alert(e.message),
    })
  }

  return (
    <div className="p-6 max-w-2xl">
      <div className="mb-6 flex items-start gap-4">
        <Link href="/dashboard/files" className="text-sm text-muted-foreground hover:text-foreground mt-1">
          ← Файлы
        </Link>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 mb-1">
            <h1 className="text-xl font-semibold truncate">{data.original_name}</h1>
            <span
              className={`inline-flex shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${FILE_STATUS_COLORS[data.status] ?? 'bg-muted text-muted-foreground'}`}
            >
              {FILE_STATUS_LABELS[data.status] ?? data.status}
            </span>
          </div>
          {isTransient && (
            <p className="text-xs text-muted-foreground">Страница обновляется автоматически...</p>
          )}
        </div>
        <div className="flex gap-2 shrink-0">
          {canDownload && isAvailable && (
            <Button size="sm" loading={downloadMutation.isPending} onClick={handleDownload}>
              Скачать
            </Button>
          )}
          {canDelete && !isDeleted && (
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

      {isQuarantined && data.quarantine_reason && (
        <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3">
          <p className="text-sm font-medium text-destructive">Файл помещён в карантин</p>
          <p className="text-xs text-muted-foreground mt-1">{data.quarantine_reason}</p>
        </div>
      )}

      <dl className="divide-y divide-border rounded-md border border-border">
        <Row label="Имя файла" value={data.original_name} />
        <Row label="Тип содержимого" value={data.content_type} />
        <Row label="Размер" value={formatFileSize(data.size_bytes)} />
        <Row
          label="Загружен"
          value={new Date(data.created_at).toLocaleString('ru-RU')}
        />
        {data.scanned_at && (
          <Row
            label="Проверен"
            value={new Date(data.scanned_at).toLocaleString('ru-RU')}
          />
        )}
        {data.checksum_sha256 && <Row label="SHA-256" value={data.checksum_sha256} mono />}
        <Row label="Object key" value={data.object_key} mono />
        <Row label="ID" value={data.id} mono />
      </dl>
    </div>
  )
}
