import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

export type Task = components['schemas']['TaskOut']
export type TaskList = components['schemas']['TaskListOut']
export type TaskCreateRequest = components['schemas']['TaskCreateIn']
export type TaskUpdateRequest = components['schemas']['TaskUpdateIn']
export type TaskStatusChangeRequest = components['schemas']['TaskStatusChangeIn']
export type TaskComment = components['schemas']['TaskCommentOut']
export type TaskCommentCreateRequest = components['schemas']['TaskCommentIn']
export type TaskAttachment = components['schemas']['TaskAttachmentOut']
export type TaskAttachFileRequest = components['schemas']['TaskAttachFileIn']

export const TASK_STATUS_LABELS: Record<string, string> = {
  open: 'Открыта',
  in_progress: 'В работе',
  done: 'Выполнена',
  cancelled: 'Отменена',
}

export const TASK_STATUS_COLORS: Record<string, string> = {
  open: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300',
  in_progress: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300',
  done: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300',
  cancelled: 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300',
}

export const TASK_PRIORITY_LABELS: Record<string, string> = {
  low: 'Низкий',
  normal: 'Нормальный',
  high: 'Высокий',
}

export const TASK_PRIORITY_COLORS: Record<string, string> = {
  low: 'bg-muted text-muted-foreground',
  normal: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300',
  high: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300',
}

type ApiErrorBody = { error?: { message?: string } }
const apiMsg = (error: unknown, fallback: string) =>
  (error as ApiErrorBody)?.error?.message ?? fallback

export const taskKeys = {
  all: ['tasks'] as const,
  lists: () => [...taskKeys.all, 'list'] as const,
  list: (params: Record<string, unknown>) => [...taskKeys.lists(), params] as const,
  details: () => [...taskKeys.all, 'detail'] as const,
  detail: (id: string) => [...taskKeys.details(), id] as const,
  comments: (id: string) => [...taskKeys.all, 'comments', id] as const,
  files: (id: string) => [...taskKeys.all, 'files', id] as const,
}

export function useTasks(params?: {
  cursor?: string
  limit?: number
  status?: string
  assigned_to_id?: string
  entity_type?: string
  entity_id?: string
  created_by_id?: string
}) {
  return useQuery({
    queryKey: taskKeys.list(params ?? {}),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/tasks/', {
        params: { query: params as Record<string, string> },
      })
      if (error) throw new Error('Ошибка загрузки задач')
      return data as TaskList
    },
  })
}

export function useTask(id: string) {
  return useQuery({
    queryKey: taskKeys.detail(id),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/tasks/{task_id}/', {
        params: { path: { task_id: id } },
      })
      if (error) throw new Error('Ошибка загрузки задачи')
      return data as Task
    },
    enabled: !!id,
  })
}

export function useTaskComments(taskId: string) {
  return useQuery({
    queryKey: taskKeys.comments(taskId),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/tasks/{task_id}/comments/', {
        params: { path: { task_id: taskId } },
      })
      if (error) throw new Error('Ошибка загрузки комментариев')
      return data as TaskComment[]
    },
    enabled: !!taskId,
  })
}

export function useTaskFiles(taskId: string) {
  return useQuery({
    queryKey: taskKeys.files(taskId),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/tasks/{task_id}/files/', {
        params: { path: { task_id: taskId } },
      })
      if (error) throw new Error('Ошибка загрузки файлов')
      return data as TaskAttachment[]
    },
    enabled: !!taskId,
  })
}

export function useCreateTask() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (payload: TaskCreateRequest) => {
      const { data, error } = await apiClient.POST('/api/v1/tasks/', {
        body: payload,
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка создания задачи'))
      return data as Task
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: taskKeys.lists() })
    },
  })
}

export function useUpdateTask(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (payload: TaskUpdateRequest) => {
      const { data, error } = await apiClient.PATCH('/api/v1/tasks/{task_id}/', {
        params: { path: { task_id: id } },
        body: payload,
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка обновления задачи'))
      return data as Task
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: taskKeys.detail(id) })
      qc.invalidateQueries({ queryKey: taskKeys.lists() })
    },
  })
}

export function useChangeTaskStatus(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (payload: TaskStatusChangeRequest) => {
      const { data, error } = await apiClient.POST('/api/v1/tasks/{task_id}/status/', {
        params: { path: { task_id: id } },
        body: payload,
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка смены статуса'))
      return data as Task
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: taskKeys.detail(id) })
      qc.invalidateQueries({ queryKey: taskKeys.lists() })
    },
  })
}

export function useCancelTask(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const { data, error } = await apiClient.POST('/api/v1/tasks/{task_id}/cancel/', {
        params: { path: { task_id: id } },
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка отмены задачи'))
      return data as Task
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: taskKeys.detail(id) })
      qc.invalidateQueries({ queryKey: taskKeys.lists() })
    },
  })
}

export function useAddComment(taskId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (payload: TaskCommentCreateRequest) => {
      const { data, error } = await apiClient.POST('/api/v1/tasks/{task_id}/comments/', {
        params: { path: { task_id: taskId } },
        body: payload,
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка добавления комментария'))
      return data as TaskComment
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: taskKeys.comments(taskId) })
    },
  })
}

export function useAttachFile(taskId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (payload: TaskAttachFileRequest) => {
      const { data, error } = await apiClient.POST('/api/v1/tasks/{task_id}/files/', {
        params: { path: { task_id: taskId } },
        body: payload,
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка прикрепления файла'))
      return data as TaskAttachment
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: taskKeys.files(taskId) })
    },
  })
}

export function useDetachFile(taskId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (fileId: string) => {
      const { error } = await apiClient.DELETE('/api/v1/tasks/{task_id}/files/{file_id}/', {
        params: { path: { task_id: taskId, file_id: fileId } },
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка открепления файла'))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: taskKeys.files(taskId) })
    },
  })
}
