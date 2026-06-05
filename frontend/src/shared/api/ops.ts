import { useQuery } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

export type OpsSnapshot = components['schemas']['OpsSnapshot']
export type OutboxStats = components['schemas']['OutboxStats']
export type SLOMetricStatus = components['schemas']['SLOMetricStatus']

export function useOpsSnapshot() {
  return useQuery({
    queryKey: ['ops', 'snapshot'],
    queryFn: async () => {
      const { data, error, response } = await apiClient.GET('/api/v1/admin/ops/')
      if (!response.ok || error) throw new Error('Ошибка загрузки метрик')
      return data as OpsSnapshot
    },
    refetchInterval: 30_000,
    staleTime: 10_000,
  })
}
