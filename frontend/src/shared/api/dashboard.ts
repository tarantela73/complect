import { useQuery } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

export type DashboardSummary = components['schemas']['DashboardSummarySchema']
export type OperationalCounts = components['schemas']['OperationalCountsSchema']
export type DirectorDashboard = components['schemas']['DirectorDashboardSchema']

type ApiErrorBody = { error?: { message?: string } }
const apiMsg = (error: unknown, fallback: string) =>
  (error as ApiErrorBody)?.error?.message ?? fallback

export const dashboardKeys = {
  summary: ['dashboard', 'summary'] as const,
  director: ['dashboard', 'director'] as const,
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

export function useDirectorDashboard() {
  return useQuery({
    queryKey: dashboardKeys.director,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/dashboard/director/')
      if (error) throw new Error(apiMsg(error, 'Ошибка загрузки дашборда директора'))
      return data as DirectorDashboard
    },
    refetchInterval: 60_000,
  })
}
