import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import { isWsConnected, WS_FALLBACK_POLL_MS } from './realtime'
import type { components } from './generated/schema'

export type FileRecord = components['schemas']['FileDetailResponse']
export type FileList = components['schemas']['FileListResponse']

export const FILE_STATUS_LABELS: Record<string, string> = {
  PENDING_UPLOAD: 'Ожидает загрузки',
  UPLOADED: 'Загружен',
  SCANNING: 'Сканируется',
  AVAILABLE: 'Доступен',
  QUARANTINED: 'Карантин',
  DELETED: 'Удалён',
}

export const FILE_STATUS_COLORS: Record<string, string> = {
  PENDING_UPLOAD: 'bg-muted text-muted-foreground',
  UPLOADED: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  SCANNING: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  AVAILABLE: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  QUARANTINED: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
  DELETED: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400',
}

export const FILE_STATUSES = Object.keys(FILE_STATUS_LABELS)

const TRANSIENT_STATUSES = new Set(['PENDING_UPLOAD', 'UPLOADED', 'SCANNING'])

type ApiErrorBody = { error?: { message?: string } }
const apiMsg = (error: unknown, fallback: string) =>
  (error as ApiErrorBody)?.error?.message ?? fallback

export const fileKeys = {
  all: ['files'] as const,
  lists: () => [...fileKeys.all, 'list'] as const,
  list: (params: Record<string, unknown>) => [...fileKeys.lists(), params] as const,
  details: () => [...fileKeys.all, 'detail'] as const,
  detail: (id: string) => [...fileKeys.details(), id] as const,
}

export function useFiles(params: { cursor?: string; limit?: number; status?: string; q?: string }) {
  return useQuery({
    queryKey: fileKeys.list(params),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/files/', {
        params: { query: params },
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка загрузки файлов'))
      return data
    },
  })
}

export function useFile(id: string) {
  return useQuery({
    queryKey: fileKeys.detail(id),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/files/{file_id}', {
        params: { path: { file_id: id } },
      })
      if (error) throw new Error(apiMsg(error, 'Файл не найден'))
      return data
    },
    refetchInterval: (query) => {
      if (!query.state.data || !TRANSIENT_STATUSES.has(query.state.data.status)) return false
      // WS active → 30s safety fallback; WS down → 3s aggressive polling
      return isWsConnected() ? WS_FALLBACK_POLL_MS : 3000
    },
  })
}

async function sha256Hex(file: File): Promise<string> {
  const buffer = await file.arrayBuffer()
  const hashBuffer = await crypto.subtle.digest('SHA-256', buffer)
  return Array.from(new Uint8Array(hashBuffer))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

export function useUploadFile() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (file: File) => {
      const { data: init, error: initErr } = await apiClient.POST('/api/v1/files/upload-init', {
        body: {
          original_name: file.name,
          content_type: file.type || 'application/octet-stream',
          size_bytes: file.size,
        },
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      if (initErr) throw new Error(apiMsg(initErr, 'Ошибка инициализации загрузки'))

      const checksum = await sha256Hex(file)

      if (!init.upload_url.startsWith('/stub/')) {
        const putRes = await fetch(init.upload_url, {
          method: 'PUT',
          body: file,
          headers: { 'Content-Type': file.type || 'application/octet-stream' },
        })
        if (!putRes.ok) throw new Error(`Ошибка загрузки файла: ${putRes.status}`)
      }

      const { data: completed, error: completeErr } = await apiClient.POST(
        '/api/v1/files/{file_id}/complete',
        {
          params: { path: { file_id: init.file_id } },
          body: { checksum_sha256: checksum },
          headers: { 'Idempotency-Key': crypto.randomUUID() },
        },
      )
      if (completeErr) throw new Error(apiMsg(completeErr, 'Ошибка подтверждения загрузки'))
      return completed
    },
    onSuccess: (newFile) => {
      qc.setQueryData(fileKeys.detail(newFile.id), newFile)
      qc.invalidateQueries({ queryKey: fileKeys.lists() })
    },
  })
}

export function useDeleteFile() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const { error } = await apiClient.DELETE('/api/v1/files/{file_id}', {
        params: { path: { file_id: id } },
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка удаления файла'))
    },
    onSuccess: (_, id) => {
      qc.removeQueries({ queryKey: fileKeys.detail(id) })
      qc.invalidateQueries({ queryKey: fileKeys.lists() })
    },
  })
}

export function useDownloadUrl() {
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error } = await apiClient.GET('/api/v1/files/{file_id}/download-url', {
        params: { path: { file_id: id } },
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка получения ссылки для скачивания'))
      return data
    },
  })
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} ГБ`
}
