import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

export type WebhookSubscription = components['schemas']['WebhookSubscriptionOut']
export type WebhookDelivery = components['schemas']['WebhookDeliveryOut']
export type WebhookStats = components['schemas']['WebhookStatsOut']
export type WebhookCreate = components['schemas']['WebhookSubscriptionCreate']
export type WebhookUpdate = components['schemas']['WebhookSubscriptionUpdate']

export const webhookKeys = {
  all: ['webhooks'] as const,
  lists: () => [...webhookKeys.all, 'list'] as const,
  list: (params: Record<string, unknown>) => [...webhookKeys.lists(), params] as const,
  detail: (id: string) => [...webhookKeys.all, 'detail', id] as const,
  deliveries: (id: string) => [...webhookKeys.all, 'deliveries', id] as const,
  stats: () => [...webhookKeys.all, 'stats'] as const,
}

type ApiErrorBody = { error?: { message?: string } }
const apiMsg = (error: unknown, fallback: string) =>
  (error as ApiErrorBody)?.error?.message ?? fallback

export function useWebhookStats() {
  return useQuery({
    queryKey: webhookKeys.stats(),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/webhooks/stats/')
      if (error) throw new Error(apiMsg(error, 'Ошибка загрузки статистики'))
      return data as WebhookStats
    },
    refetchInterval: 30_000,
    staleTime: 10_000,
  })
}

export function useWebhooks(params?: { cursor?: string; limit?: number }) {
  return useQuery({
    queryKey: webhookKeys.list(params ?? {}),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/webhooks/', {
        params: { query: params },
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка загрузки подписок'))
      return data
    },
  })
}

export function useWebhookDeliveries(subId: string, params?: { cursor?: string; limit?: number }) {
  return useQuery({
    queryKey: webhookKeys.deliveries(subId),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/webhooks/{sub_id}/deliveries/', {
        params: { path: { sub_id: subId }, query: params },
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка загрузки доставок'))
      return data
    },
    enabled: !!subId,
  })
}

export function useCreateWebhook() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: WebhookCreate) => {
      const { data, error } = await apiClient.POST('/api/v1/webhooks/', { body })
      if (error) throw new Error(apiMsg(error, 'Ошибка создания подписки'))
      return data as WebhookSubscription
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: webhookKeys.lists() }),
  })
}

export function useToggleWebhook() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, is_active }: { id: string; is_active: boolean }) => {
      const { data, error } = await apiClient.PATCH('/api/v1/webhooks/{sub_id}/', {
        params: { path: { sub_id: id } },
        body: { is_active },
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка обновления подписки'))
      return data as WebhookSubscription
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: webhookKeys.lists() })
      qc.invalidateQueries({ queryKey: webhookKeys.stats() })
    },
  })
}

export function useDeleteWebhook() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const { error } = await apiClient.DELETE('/api/v1/webhooks/{sub_id}/', {
        params: { path: { sub_id: id } },
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка удаления подписки'))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: webhookKeys.lists() })
      qc.invalidateQueries({ queryKey: webhookKeys.stats() })
    },
  })
}

export function useReplayDelivery(subId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (deliveryId: string) => {
      const { data, error } = await apiClient.POST(
        '/api/v1/webhooks/{sub_id}/deliveries/{delivery_id}/replay/',
        { params: { path: { sub_id: subId, delivery_id: deliveryId } } },
      )
      if (error) throw new Error(apiMsg(error, 'Ошибка повторной отправки'))
      return data as WebhookDelivery
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: webhookKeys.deliveries(subId) })
      qc.invalidateQueries({ queryKey: webhookKeys.stats() })
    },
  })
}
