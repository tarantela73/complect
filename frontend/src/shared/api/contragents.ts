import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

export type Contragent = components['schemas']['ContragentResponse']
export type ContragentList = components['schemas']['ContragentListResponse']
export type ContragentCreateRequest = components['schemas']['ContragentCreateRequest']
export type ContragentUpdateRequest = components['schemas']['ContragentUpdateRequest']

export const CONTRAGENT_TYPE_LABELS: Record<string, string> = {
  LEGAL_ENTITY: 'Юридическое лицо',
  ENTREPRENEUR: 'Индивидуальный предприниматель',
  INDIVIDUAL: 'Физическое лицо',
}

export const CONTRAGENT_TYPES = Object.keys(CONTRAGENT_TYPE_LABELS)

type ApiErrorBody = { error?: { message?: string } }
const apiMsg = (error: unknown, fallback: string) =>
  (error as ApiErrorBody)?.error?.message ?? fallback

export const contragentKeys = {
  all: ['contragents'] as const,
  lists: () => [...contragentKeys.all, 'list'] as const,
  list: (params: Record<string, unknown>) => [...contragentKeys.lists(), params] as const,
  details: () => [...contragentKeys.all, 'detail'] as const,
  detail: (id: string) => [...contragentKeys.details(), id] as const,
}

export function useContragents(params?: {
  cursor?: string
  limit?: number
  contragent_type?: string
  inn?: string
  q?: string
}) {
  return useQuery({
    queryKey: contragentKeys.list(params ?? {}),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/contragents/', {
        params: { query: params },
      })
      if (error) throw new Error('Ошибка загрузки контрагентов')
      return data as ContragentList
    },
  })
}

export function useContragent(id: string) {
  return useQuery({
    queryKey: contragentKeys.detail(id),
    queryFn: async () => {
      const { data, error, response } = await apiClient.GET('/api/v1/contragents/{contragent_id}/', {
        params: { path: { contragent_id: id } },
      })
      if (response.status === 404) throw new Error('NOT_FOUND')
      if (error) throw new Error('Ошибка загрузки контрагента')
      return data as Contragent
    },
    enabled: !!id,
  })
}

export function useCreateContragent() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: ContragentCreateRequest) => {
      const { data, error, response } = await apiClient.POST('/api/v1/contragents/', {
        body,
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      if (response.status === 400 || error) {
        throw new Error(apiMsg(error, 'Ошибка создания'))
      }
      return data as Contragent
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: contragentKeys.lists() }),
  })
}

export function useUpdateContragent(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: ContragentUpdateRequest) => {
      const { data, error, response } = await apiClient.PATCH(
        '/api/v1/contragents/{contragent_id}/',
        { params: { path: { contragent_id: id } }, body, headers: { 'Idempotency-Key': crypto.randomUUID() } },
      )
      if (response.status === 422) throw new Error(apiMsg(error, 'Бизнес-правило нарушено'))
      if (error) throw new Error('Ошибка обновления')
      return data as Contragent
    },
    onSuccess: (updated) => {
      qc.setQueryData(contragentKeys.detail(id), updated)
      qc.invalidateQueries({ queryKey: contragentKeys.lists() })
    },
  })
}

export function useDeleteContragent() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await apiClient.DELETE(
        '/api/v1/contragents/{contragent_id}/',
        { params: { path: { contragent_id: id } }, headers: { 'Idempotency-Key': crypto.randomUUID() } },
      )
      if (response.status === 422) throw new Error(apiMsg(error, 'Удаление заблокировано'))
      if (response.status !== 204 && error) throw new Error('Ошибка удаления')
    },
    onSuccess: (_data, id) => {
      qc.removeQueries({ queryKey: contragentKeys.detail(id) })
      qc.invalidateQueries({ queryKey: contragentKeys.lists() })
    },
  })
}
