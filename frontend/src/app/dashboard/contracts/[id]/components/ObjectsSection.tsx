'use client'

import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/shared/api/client'
import { OBJECT_STATUS_LABELS } from '@/shared/api/objects'
import type { components } from '@/shared/api/generated/schema'

type SupplyObject = components['schemas']['SupplyObjectDetailResponse']

const STATUS_COLORS: Record<string, string> = {
  NEW: 'bg-muted text-muted-foreground',
  IN_PROGRESS: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  PAUSED: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  COMPLETED: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  CANCELLED: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
}

function useObjectsByContract(contractId: string) {
  return useQuery({
    queryKey: ['objects', 'by-contract', contractId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/objects/by-contract/{contract_id}/', {
        params: { path: { contract_id: contractId } },
      })
      if (error) throw new Error('Ошибка загрузки объектов')
      return (data as { results: SupplyObject[] }).results
    },
    enabled: !!contractId,
  })
}

export function ObjectsSection({ contractId }: { contractId: string }) {
  const { data: objects, isLoading } = useObjectsByContract(contractId)

  if (isLoading) {
    return (
      <div className="mt-6">
        <h2 className="text-sm font-medium mb-3">Объекты</h2>
        <p className="text-sm text-muted-foreground">Загрузка...</p>
      </div>
    )
  }

  if (!objects || objects.length === 0) return null

  return (
    <div className="mt-6">
      <h2 className="text-sm font-medium mb-3">Объекты ({objects.length})</h2>
      <div className="rounded-md border border-border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Название</th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Адрес</th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Статус</th>
              <th className="px-4 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {objects.map((obj) => (
              <tr key={obj.id} className="border-b border-border last:border-0 hover:bg-muted/20">
                <td className="px-4 py-3">
                  <Link href={`/dashboard/objects/${obj.id}`} className="font-medium hover:underline">
                    {obj.name}
                  </Link>
                </td>
                <td className="px-4 py-3 text-muted-foreground">{obj.address || '—'}</td>
                <td className="px-4 py-3">
                  <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[obj.status] ?? 'bg-muted text-muted-foreground'}`}>
                    {OBJECT_STATUS_LABELS[obj.status] ?? obj.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  <Link href={`/dashboard/objects/${obj.id}`}>
                    <span className="text-primary hover:underline text-xs">→</span>
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
