import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

export type SupplyObject = components['schemas']['SupplyObjectDetailResponse']
export type ObjectStage = components['schemas']['ObjectStageResponse']
export type UserDisplay = components['schemas']['UserDisplaySchema']
export type ObjectList = components['schemas']['SupplyObjectListResponse']
export type ObjectCreateRequest = components['schemas']['SupplyObjectCreateRequest']
export type ObjectUpdateRequest = components['schemas']['SupplyObjectUpdateRequest']
export type StageUpdateRequest = components['schemas']['StageUpdateRequest']

export const RESOURCE_TYPE_LABELS: Record<string, string> = {
  ELECTRICITY: 'Электроснабжение',
  GAS: 'Газоснабжение',
  WATER: 'Водоснабжение',
  HEAT: 'Теплоснабжение',
  OTHER: 'Прочее',
}

export const OBJECT_STATUS_LABELS: Record<string, string> = {
  NEW: 'Новый',
  IN_PROGRESS: 'В работе',
  PAUSED: 'Приостановлен',
  COMPLETED: 'Завершён',
  CANCELLED: 'Отменён',
}

export const STAGE_KEY_LABELS: Record<string, string> = {
  REQUEST_RECEIVED: 'Получена заявка',
  SITE_VISIT: 'Выезд на объект',
  COST_ESTIMATION: 'Расчёт стоимости',
  CONTRACT_SIGNING: 'Подписание договора',
  INITIAL_PAYMENT: 'Первоначальная оплата',
  INSTALLATION: 'Монтаж',
  TESTING: 'Испытания',
  FINAL_PAYMENT: 'Финальная оплата',
  ACT_SIGNING: 'Подписание акта',
  CLOSURE: 'Закрытие',
}

export const STAGE_STATUS_LABELS: Record<string, string> = {
  PENDING: 'Ожидает',
  IN_PROGRESS: 'В работе',
  COMPLETED: 'Завершён',
  SKIPPED: 'Пропущен',
}

export const OBJECT_STATUS_COLORS: Record<string, string> = {
  NEW: 'secondary',
  IN_PROGRESS: 'default',
  PAUSED: 'outline',
  COMPLETED: 'default',
  CANCELLED: 'destructive',
}

type ApiErrorBody = { error?: { message?: string } }
const apiMsg = (error: unknown, fallback: string) =>
  (error as ApiErrorBody)?.error?.message ?? fallback

export const objectKeys = {
  all: ['objects'] as const,
  lists: () => [...objectKeys.all, 'list'] as const,
  list: (params: Record<string, unknown>) => [...objectKeys.lists(), params] as const,
  details: () => [...objectKeys.all, 'detail'] as const,
  detail: (id: string) => [...objectKeys.details(), id] as const,
}

export function useObjects(params?: {
  cursor?: string
  limit?: number
  status?: string
  resource_type?: string
  object_type?: string
  manager_id?: string
  contract_id?: string
}) {
  return useQuery({
    queryKey: objectKeys.list(params ?? {}),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/objects/', {
        params: { query: params as Record<string, string | undefined> },
      })
      if (error) throw new Error('Ошибка загрузки объектов')
      return data as ObjectList
    },
  })
}

export function useObject(id: string) {
  return useQuery({
    queryKey: objectKeys.detail(id),
    queryFn: async () => {
      const { data, error, response } = await apiClient.GET('/api/v1/objects/{object_id}/', {
        params: { path: { object_id: id } },
      })
      if (response.status === 403) throw new Error('NOT_FOUND')
      if (error) throw new Error('Ошибка загрузки объекта')
      return data as SupplyObject
    },
    enabled: !!id,
  })
}

export function useCreateObject() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: ObjectCreateRequest) => {
      const { data, error, response } = await apiClient.POST('/api/v1/objects/', {
        body,
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      if (response.status === 400 || error) throw new Error(apiMsg(error, 'Ошибка создания объекта'))
      return data as SupplyObject
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: objectKeys.lists() }),
  })
}

export function useUpdateObject(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: ObjectUpdateRequest) => {
      const { data, error, response } = await apiClient.PATCH('/api/v1/objects/{object_id}/', {
        params: { path: { object_id: id } },
        body,
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      if (response.status === 422) throw new Error(apiMsg(error, 'Обновление недоступно'))
      if (error) throw new Error('Ошибка обновления объекта')
      return data as SupplyObject
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: objectKeys.detail(id) })
      qc.invalidateQueries({ queryKey: objectKeys.lists() })
    },
  })
}

export type ObjectContractLink = components['schemas']['ObjectContractResponse']

export const objectContractKeys = {
  list: (objectId: string) => ['objects', objectId, 'contracts'] as const,
}

export function useObjectContracts(objectId: string) {
  return useQuery({
    queryKey: objectContractKeys.list(objectId),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/objects/{object_id}/contracts/', {
        params: { path: { object_id: objectId } },
      })
      if (error) throw new Error('Ошибка загрузки договоров')
      return data as ObjectContractLink[]
    },
    enabled: !!objectId,
  })
}

export function useLinkObjectContract(objectId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (contractId: string) => {
      const { error } = await apiClient.POST('/api/v1/objects/{object_id}/contracts/', {
        params: { path: { object_id: objectId } },
        body: { contract_id: contractId },
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      if (error) throw new Error('Ошибка привязки договора')
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: objectContractKeys.list(objectId) }),
  })
}

export function useUnlinkObjectContract(objectId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (contractId: string) => {
      const { error } = await apiClient.DELETE(
        '/api/v1/objects/{object_id}/contracts/{contract_id}/',
        {
          params: { path: { object_id: objectId, contract_id: contractId } },
          headers: { 'Idempotency-Key': crypto.randomUUID() },
        },
      )
      if (error) throw new Error('Ошибка отвязки договора')
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: objectContractKeys.list(objectId) }),
  })
}

export function useUpdateObjectStage(objectId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ stageKey, body }: { stageKey: string; body: StageUpdateRequest }) => {
      const { data, error, response } = await apiClient.PATCH(
        '/api/v1/objects/{object_id}/stages/{stage_key}/',
        {
          params: { path: { object_id: objectId, stage_key: stageKey } },
          body,
          headers: { 'Idempotency-Key': crypto.randomUUID() },
        },
      )
      if (response.status === 422) throw new Error(apiMsg(error, 'Переход недопустим'))
      if (error) throw new Error('Ошибка обновления этапа')
      return data as SupplyObject
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: objectKeys.detail(objectId) }),
  })
}
