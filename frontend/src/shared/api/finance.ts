import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

export type Transaction = components['schemas']['TransactionResponse']
export type TransactionList = components['schemas']['TransactionListResponse']
export type TransactionPostRequest = components['schemas']['TransactionPostRequest']

export const TRANSACTION_TYPE_LABELS: Record<string, string> = {
  PAYMENT: 'Оплата',
  REFUND: 'Возврат',
  CORRECTION: 'Корректировка',
}

export const TRANSACTION_STATUS_LABELS: Record<string, string> = {
  POSTED: 'Проведена',
  REVERSED: 'Сторнирована',
}

export const TRANSACTION_TYPES = Object.keys(TRANSACTION_TYPE_LABELS)
export const TRANSACTION_STATUSES = Object.keys(TRANSACTION_STATUS_LABELS)

type ApiErrorBody = { error?: { message?: string } }
const apiMsg = (error: unknown, fallback: string) =>
  (error as ApiErrorBody)?.error?.message ?? fallback

export const financeKeys = {
  all: ['finance'] as const,
  lists: () => [...financeKeys.all, 'list'] as const,
  list: (params: Record<string, unknown>) => [...financeKeys.lists(), params] as const,
}

export function useTransactions(params?: {
  cursor?: string
  limit?: number
  contract_id?: string
  status?: string
  transaction_type?: string
}) {
  return useQuery({
    queryKey: financeKeys.list(params ?? {}),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/finance/transactions/', {
        params: { query: params },
      })
      if (error) throw new Error('Ошибка загрузки транзакций')
      return data as TransactionList
    },
  })
}

export function usePostTransaction() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: TransactionPostRequest) => {
      const { data, error, response } = await apiClient.POST('/api/v1/finance/transactions/', {
        body,
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      if (response.status === 400 || error) {
        throw new Error(apiMsg(error, 'Ошибка проводки'))
      }
      return data as Transaction
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: financeKeys.lists() }),
  })
}

export function useReverseTransaction() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (txId: string) => {
      const { data, error, response } = await apiClient.POST(
        '/api/v1/finance/transactions/{tx_id}/reverse/',
        {
          params: { path: { tx_id: txId } },
          headers: { 'Idempotency-Key': crypto.randomUUID() },
        },
      )
      if (response.status === 403) throw new Error(apiMsg(error, 'Реверс запрещён'))
      if (error) throw new Error('Ошибка реверса')
      return data as Transaction
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: financeKeys.lists() }),
  })
}
