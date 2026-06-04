'use client'

import { useRef, useState } from 'react'
import Link from 'next/link'
import { useCan } from '@/shared/auth/hooks'
import {
  useFiles,
  useUploadFile,
  useDeleteFile,
  FILE_STATUS_LABELS,
  FILE_STATUS_COLORS,
  FILE_STATUSES,
  formatFileSize,
  type FileRecord,
} from '@/shared/api/files'
import { useFileStatusSocket } from '@/shared/api/realtime'
import { Button } from '@/shared/ui/button'

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${FILE_STATUS_COLORS[status] ?? 'bg-muted text-muted-foreground'}`}
    >
      {FILE_STATUS_LABELS[status] ?? status}
    </span>
  )
}

function FileRow({
  file,
  canDelete,
  onDelete,
}: {
  file: FileRecord
  canDelete: boolean
  onDelete: (id: string) => void
}) {
  return (
    <tr className="border-b border-border hover:bg-muted/30 transition-colors">
      <td className="px-4 py-3 text-sm max-w-xs truncate">
        <Link
          href={`/dashboard/files/${file.id}`}
          className="font-medium hover:underline text-foreground"
        >
          {file.original_name}
        </Link>
      </td>
      <td className="px-4 py-3 text-xs text-muted-foreground">{file.content_type}</td>
      <td className="px-4 py-3 text-sm text-right font-mono">{formatFileSize(file.size_bytes)}</td>
      <td className="px-4 py-3">
        <StatusBadge status={file.status} />
      </td>
      <td className="px-4 py-3 text-xs text-muted-foreground">
        {new Date(file.created_at).toLocaleString('ru-RU')}
      </td>
      <td className="px-4 py-3 text-sm text-right">
        <div className="flex justify-end gap-1">
          <Link href={`/dashboard/files/${file.id}`}>
            <Button variant="ghost" size="sm">→</Button>
          </Link>
          {canDelete && (
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => {
                if (confirm(`Удалить файл «${file.original_name}»?`)) onDelete(file.id)
              }}
            >
              ✕
            </Button>
          )}
        </div>
      </td>
    </tr>
  )
}

export default function FilesPage() {
  useFileStatusSocket()

  const canCreate = useCan('files.create')
  const canDelete = useCan('files.delete')

  const [statusFilter, setStatusFilter] = useState('')
  const [cursors, setCursors] = useState<string[]>([])
  const [uploadError, setUploadError] = useState('')
  const currentCursor = cursors.at(-1)

  const fileInputRef = useRef<HTMLInputElement>(null)

  const { data, isLoading, isError } = useFiles({
    cursor: currentCursor,
    limit: 20,
    status: statusFilter || undefined,
  })

  const uploadMutation = useUploadFile()
  const deleteMutation = useDeleteFile()

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploadError('')
    uploadMutation.mutate(file, {
      onError: (err) => setUploadError(err.message),
    })
    e.target.value = ''
  }

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Файлы</h1>
        {canCreate && (
          <>
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              onChange={handleFileSelect}
            />
            <Button
              size="sm"
              loading={uploadMutation.isPending}
              onClick={() => fileInputRef.current?.click()}
            >
              Загрузить файл
            </Button>
          </>
        )}
      </div>

      {uploadError && (
        <p className="mb-4 text-sm text-destructive">{uploadError}</p>
      )}

      {uploadMutation.isSuccess && (
        <p className="mb-4 text-sm text-green-600">
          Файл загружен.{' '}
          <Link href={`/dashboard/files/${uploadMutation.data.id}`} className="underline">
            Открыть
          </Link>
        </p>
      )}

      <div className="mb-4">
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setCursors([]) }}
          className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <option value="">Все статусы</option>
          {FILE_STATUSES.filter((s) => s !== 'DELETED').map((s) => (
            <option key={s} value={s}>{FILE_STATUS_LABELS[s]}</option>
          ))}
        </select>
      </div>

      {isError && (
        <p className="text-sm text-destructive">Ошибка загрузки файлов</p>
      )}

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Загрузка...</p>
      ) : (
        <>
          <div className="rounded-md border border-border overflow-hidden">
            <table className="w-full">
              <thead className="bg-muted/50">
                <tr>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Имя файла</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Тип</th>
                  <th className="px-4 py-2.5 text-right text-xs font-medium text-muted-foreground">Размер</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Статус</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Загружен</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {data?.results.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-sm text-muted-foreground">
                      Файлы не найдены
                    </td>
                  </tr>
                ) : (
                  data?.results.map((f) => (
                    <FileRow
                      key={f.id}
                      file={f}
                      canDelete={canDelete}
                      onDelete={(id) => deleteMutation.mutate(id)}
                    />
                  ))
                )}
              </tbody>
            </table>
          </div>

          <div className="mt-4 flex gap-2">
            {cursors.length > 0 && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setCursors((prev) => prev.slice(0, -1))}
              >
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
    </div>
  )
}
