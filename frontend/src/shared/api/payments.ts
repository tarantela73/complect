import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuthStore } from '@/shared/auth/store'
import { env } from '@/shared/config/env'
import { apiClient } from './client'
import type { components } from './generated/schema'

// ── Types ─────────────────────────────────────────────────────────────────────

export type BankStatementLine = components['schemas']['BankStatementLineOut']
export type BankStatement     = components['schemas']['BankStatementOut']
export type BankStatementDetail = components['schemas']['BankStatementDetailOut']

// ── Query keys ────────────────────────────────────────────────────────────────

export const paymentKeys = {
  all: ['payments'] as const,
  statements: () => [...paymentKeys.all, 'statements'] as const,
  statement: (id: string) => [...paymentKeys.all, 'statement', id] as const,
}

// ── Error helper ──────────────────────────────────────────────────────────────

type ApiErrorBody = { error?: { message?: string } }
const apiMsg = (error: unknown, fallback: string) =>
  (error as ApiErrorBody)?.error?.message ?? fallback

// ── Hooks ──────────────────────────────────────────────────────────────────────

export function useBankStatements(status?: string) {
  return useQuery({
    queryKey: [...paymentKeys.statements(), status],
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/payments/statements/', {
        params: { query: status ? { status } : undefined },
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка загрузки выписок'))
      return data
    },
  })
}

export function useBankStatement(id: string) {
  return useQuery({
    queryKey: paymentKeys.statement(id),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/payments/statements/{stmt_id}/', {
        params: { path: { stmt_id: id } },
      })
      if (error) throw new Error(apiMsg(error, 'Ошибка загрузки выписки'))
      return data
    },
    enabled: !!id,
  })
}

export function useUploadStatement() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (file: File) => {
      const token = useAuthStore.getState().accessToken
      const formData = new FormData()
      formData.append('file', file)
      const resp = await fetch(`${env.NEXT_PUBLIC_API_URL}/api/v1/payments/statements/`, {
        method: 'POST',
        body: formData,
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          'Idempotency-Key': crypto.randomUUID(),
        },
      })
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}))
        throw new Error(body?.error?.message ?? 'Ошибка загрузки файла')
      }
      return (await resp.json()) as BankStatement
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: paymentKeys.statements() }),
  })
}

export function useConfirmStatement() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (stmtId: string) => {
      const { data, error } = await apiClient.POST(
        '/api/v1/payments/statements/{stmt_id}/confirm/',
        { params: { path: { stmt_id: stmtId } } },
      )
      if (error) throw new Error(apiMsg(error, 'Ошибка подтверждения'))
      return data
    },
    onSuccess: (_, stmtId) => {
      qc.invalidateQueries({ queryKey: paymentKeys.statements() })
      qc.invalidateQueries({ queryKey: paymentKeys.statement(stmtId) })
    },
  })
}

export function useCancelStatement() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (stmtId: string) => {
      const { data, error } = await apiClient.POST(
        '/api/v1/payments/statements/{stmt_id}/cancel/',
        { params: { path: { stmt_id: stmtId } } },
      )
      if (error) throw new Error(apiMsg(error, 'Ошибка отмены'))
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: paymentKeys.statements() }),
  })
}
