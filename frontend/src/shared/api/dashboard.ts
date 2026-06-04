import { useQuery } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

export type DashboardSummary = components['schemas']['DashboardSummarySchema']

type ApiErrorBody = { error?: { message?: string } }
const apiMsg = (error: unknown, fallback: string) =>
  (error as ApiErrorBody)?.error?.message ?? fallback

export const dashboardKeys = {
  summary: ['dashboard', 'summary'] as const,
}

export function useDashboardSummary() {
  return useQuery({
    queryKey: dashboardKeys.summary,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/dashboard/')
      if (error) throw new Error(apiMsg(error, 'Ошибка загрузки сводки'))
      return data as DashboardSummary
    },
    refetchInterval: 60_000,
  })
}
