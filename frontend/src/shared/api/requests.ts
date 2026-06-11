import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

export type RequestItem = components['schemas']['RequestResponse']
export type RequestList = components['schemas']['RequestListResponse']
export type RequestCreateRequest = components['schemas']['RequestCreateRequest']
export type RequestAssignRequest = components['schemas']['RequestAssignRequest']

export const REQUEST_STATUS_LABELS: Record<string, string> = {
  NEW: 'Новая',
  ASSIGNED: 'Назначена',
  PROCESSING: 'В обработке',
  CONVERTED: 'Конвертирована',
  CLOSED: 'Закрыта',
}

export const REQUEST_SOURCE_LABELS: Record<string, string> = {
  WEBSITE: 'Сайт',
  MANUAL: 'Ручной ввод',
  PHONE: 'Телефон',
}

export const REQUEST_RESOURCE_LABELS: Record<string, string> = {
  ELECTRICITY: 'Электроснабжение',
  GAS: 'Газоснабжение',
  WATER: 'Водоснабжение',
  HEAT: 'Теплоснабжение',
  OTHER: 'Прочее',
}

export const REQUEST_STATUS_COLORS: Record<string, string> = {
  NEW: 'bg-muted text-muted-foreground',
  ASSIGNED: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  PROCESSING: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  CONVERTED: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  CLOSED: 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400',
}

type ApiErrorBody = { error?: { message?: string } }
const apiMsg = (error: unknown, fallback: string) =>
  (error as ApiErrorBody)?.error?.message ?? fallback

export const requestKeys = {
  all: ['requests'] as const,
  lists: () => [...requestKeys.all, 'list'] as const,
  list: (params: Record<string, unknown>) => [...requestKeys.lists(), params] as const,
  details: () => [...requestKeys.all, 'detail'] as const,
  detail: (id: string) => [...requestKeys.details(), id] as const,
}

export function useRequests(params?: {
  cursor?: string
  limit?: number
  status?: string
  resource_type?: string
  manager_id?: string
}) {
  return useQuery({
    queryKey: requestKeys.list(params ?? {}),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/requests/', {
        params: { query: params as Record<string, string | undefined> },
      })
      if (error) throw new Error('Ошибка загрузки заявок')
      return data as RequestList
    },
  })
}

export function useRequest(id: string) {
  return useQuery({
    queryKey: requestKeys.detail(id),
    queryFn: async () => {
      const { data, error, response } = await apiClient.GET('/api/v1/requests/{request_id}/', {
        params: { path: { request_id: id } },
      })
      if (response.status === 404) throw new Error('NOT_FOUND')
      if (error) throw new Error('Ошибка загрузки заявки')
      return data as RequestItem
    },
    enabled: !!id,
  })
}

export function useCreateRequest() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: RequestCreateRequest) => {
      const { data, error, response } = await apiClient.POST('/api/v1/requests/', {
        body,
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      if (response.status === 400 || error) throw new Error(apiMsg(error, 'Ошибка создания заявки'))
      return data as RequestItem
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: requestKeys.lists() }),
  })
}

export function useAssignRequest(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: RequestAssignRequest) => {
      const { data, error, response } = await apiClient.POST(
        '/api/v1/requests/{request_id}/assign/',
        {
          params: { path: { request_id: id } },
          body,
          headers: { 'Idempotency-Key': crypto.randomUUID() },
        },
      )
      if (response.status === 422) throw new Error(apiMsg(error, 'Назначение невозможно'))
      if (error) throw new Error('Ошибка назначения')
      return data as RequestItem
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: requestKeys.detail(id) })
      qc.invalidateQueries({ queryKey: requestKeys.lists() })
    },
  })
}

export function useProcessRequest(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } = await apiClient.POST(
        '/api/v1/requests/{request_id}/process/',
        {
          params: { path: { request_id: id } },
          headers: { 'Idempotency-Key': crypto.randomUUID() },
        },
      )
      if (response.status === 422) throw new Error(apiMsg(error, 'Переход невозможен'))
      if (error) throw new Error('Ошибка')
      return data as RequestItem
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: requestKeys.detail(id) })
      qc.invalidateQueries({ queryKey: requestKeys.lists() })
    },
  })
}

export function useConvertRequest(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } = await apiClient.POST(
        '/api/v1/requests/{request_id}/convert/',
        {
          params: { path: { request_id: id } },
          headers: { 'Idempotency-Key': crypto.randomUUID() },
        },
      )
      if (response.status === 422) throw new Error(apiMsg(error, 'Конвертация невозможна'))
      if (error) throw new Error('Ошибка конвертации')
      return data as { object_id: string }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: requestKeys.detail(id) })
      qc.invalidateQueries({ queryKey: requestKeys.lists() })
    },
  })
}

export function useCloseRequest(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } = await apiClient.POST(
        '/api/v1/requests/{request_id}/close/',
        {
          params: { path: { request_id: id } },
          headers: { 'Idempotency-Key': crypto.randomUUID() },
        },
      )
      if (response.status === 422) throw new Error(apiMsg(error, 'Закрытие невозможно'))
      if (error) throw new Error('Ошибка закрытия')
      return data as RequestItem
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: requestKeys.detail(id) })
      qc.invalidateQueries({ queryKey: requestKeys.lists() })
    },
  })
}
