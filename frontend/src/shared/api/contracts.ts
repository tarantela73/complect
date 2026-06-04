import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

export type Contract = components['schemas']['ContractResponseSchema']
export type ContractList = components['schemas']['ContractListResponse']
export type ContractCreateRequest = components['schemas']['ContractCreateRequestSchema']
export type ContractUpdateRequest = components['schemas']['ContractUpdateSchema']
export type ContractEventLogEntry = components['schemas']['ContractEventLogEntrySchema']
export type ContractEventLogResponse = components['schemas']['ContractEventLogResponse']

export const CONTRACT_STATUS_LABELS: Record<string, string> = {
  DRAFT: 'Черновик',
  ACTIVE: 'Активен',
  LEGAL_HOLD: 'Юр. удержание',
  CLOSED: 'Закрыт',
}

export const RESOURCE_TYPE_LABELS: Record<string, string> = {
  ELECTRICITY: 'Электроснабжение',
  GAS: 'Газоснабжение',
  WATER: 'Водоснабжение',
  HEAT: 'Теплоснабжение',
  OTHER: 'Прочее',
}

export const RESOURCE_TYPES = Object.keys(RESOURCE_TYPE_LABELS)
export const CONTRACT_STATUSES = Object.keys(CONTRACT_STATUS_LABELS)

type ApiErrorBody = { error?: { message?: string } }
const apiMsg = (error: unknown, fallback: string) =>
  (error as ApiErrorBody)?.error?.message ?? fallback

export const contractKeys = {
  all: ['contracts'] as const,
  lists: () => [...contractKeys.all, 'list'] as const,
  list: (params: Record<string, unknown>) => [...contractKeys.lists(), params] as const,
  details: () => [...contractKeys.all, 'detail'] as const,
  detail: (id: string) => [...contractKeys.details(), id] as const,
  events: (id: string) => [...contractKeys.all, 'events', id] as const,
}

export function useContracts(params?: {
  cursor?: string
  limit?: number
  status?: string
  resource_type?: string
}) {
  return useQuery({
    queryKey: contractKeys.list(params ?? {}),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/contracts/', {
        params: { query: params },
      })
      if (error) throw new Error('Ошибка загрузки договоров')
      return data as ContractList
    },
  })
}

export function useContract(id: string) {
  return useQuery({
    queryKey: contractKeys.detail(id),
    queryFn: async () => {
      const { data, error, response } = await apiClient.GET('/api/v1/contracts/{contract_id}/', {
        params: { path: { contract_id: id } },
      })
      if (response.status === 404) throw new Error('NOT_FOUND')
      if (error) throw new Error('Ошибка загрузки договора')
      return data as Contract
    },
    enabled: !!id,
  })
}

export function useCreateContract() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: ContractCreateRequest) => {
      const { data, error, response } = await apiClient.POST('/api/v1/contracts/', {
        body,
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      if (response.status === 400 || error) {
        throw new Error(apiMsg(error, 'Ошибка создания'))
      }
      return data as Contract
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: contractKeys.lists() }),
  })
}

export function useUpdateContract(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: ContractUpdateRequest) => {
      const { data, error, response } = await apiClient.PATCH(
        '/api/v1/contracts/{contract_id}/',
        { params: { path: { contract_id: id } }, body, headers: { 'Idempotency-Key': crypto.randomUUID() } },
      )
      if (response.status === 422) throw new Error(apiMsg(error, 'Редактирование недоступно'))
      if (error) throw new Error('Ошибка обновления')
      return data as Contract
    },
    onSuccess: (updated) => {
      qc.setQueryData(contractKeys.detail(id), updated)
      qc.invalidateQueries({ queryKey: contractKeys.lists() })
    },
  })
}

export function useActivateContract() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error, response } = await apiClient.POST(
        '/api/v1/contracts/{contract_id}/activate/',
        { params: { path: { contract_id: id } }, headers: { 'Idempotency-Key': crypto.randomUUID() } },
      )
      if (response.status === 422) throw new Error(apiMsg(error, 'Активация невозможна'))
      if (error) throw new Error('Ошибка активации')
      return data as Contract
    },
    onSuccess: (updated, id) => {
      qc.setQueryData(contractKeys.detail(id), updated)
      qc.invalidateQueries({ queryKey: contractKeys.lists() })
    },
  })
}

export function useContractEvents(id: string) {
  return useQuery({
    queryKey: contractKeys.events(id),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/contracts/{contract_id}/events/', {
        params: { path: { contract_id: id } },
      })
      if (error) throw new Error('Ошибка загрузки истории')
      return data as ContractEventLogResponse
    },
    enabled: !!id,
  })
}

export function useCloseContract() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error, response } = await apiClient.POST(
        '/api/v1/contracts/{contract_id}/close/',
        { params: { path: { contract_id: id } }, headers: { 'Idempotency-Key': crypto.randomUUID() } },
      )
      if (response.status === 400) throw new Error(apiMsg(error, 'Закрытие невозможно'))
      if (error) throw new Error('Ошибка закрытия')
      return data as Contract
    },
    onSuccess: (updated, id) => {
      qc.setQueryData(contractKeys.detail(id), updated)
      qc.invalidateQueries({ queryKey: contractKeys.lists() })
    },
  })
}
