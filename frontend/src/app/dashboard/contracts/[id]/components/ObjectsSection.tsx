'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/shared/api/client'
import {
  OBJECT_STATUS_LABELS,
  useObjects,
  useLinkContractObject,
  useUnlinkContractObject,
} from '@/shared/api/objects'
import { useCan } from '@/shared/auth/hooks'
import { Button } from '@/shared/ui/button'
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

function LinkObjectModal({
  contractId,
  linkedIds,
  onClose,
}: {
  contractId: string
  linkedIds: Set<string>
  onClose: () => void
}) {
  const link = useLinkContractObject(contractId)
  const { data: allObjects, isLoading } = useObjects({ limit: 100 })
  const [selectedId, setSelectedId] = useState('')

  const available = (allObjects?.results ?? []).filter((o) => !linkedIds.has(o.id))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedId) return
    try {
      await link.mutateAsync(selectedId)
      onClose()
    } catch (err) {
      alert((err as Error).message)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-sm rounded-lg bg-background p-6 shadow-xl">
        <h2 className="mb-4 text-base font-semibold">Привязать объект</h2>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Объект</label>
            {isLoading ? (
              <p className="text-sm text-muted-foreground">Загрузка...</p>
            ) : available.length === 0 ? (
              <p className="text-sm text-muted-foreground">Все объекты уже привязаны</p>
            ) : (
              <select
                required
                value={selectedId}
                onChange={(e) => setSelectedId(e.target.value)}
                className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value="">— выберите —</option>
                {available.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
            )}
          </div>
          {link.isError && (
            <p className="text-xs text-destructive">{(link.error as Error).message}</p>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>Отмена</Button>
            <Button
              type="submit"
              size="sm"
              disabled={!selectedId || link.isPending || available.length === 0}
            >
              {link.isPending ? 'Привязка...' : 'Привязать'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}

export function ObjectsSection({ contractId }: { contractId: string }) {
  const { data: objects, isLoading } = useObjectsByContract(contractId)
  const unlink = useUnlinkContractObject(contractId)
  const canWrite = useCan('contracts.write')
  const [showLink, setShowLink] = useState(false)

  const linkedIds = new Set((objects ?? []).map((o) => o.id))

  if (isLoading) {
    return (
      <div className="mt-6">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium">Объекты</h2>
        </div>
        <p className="text-sm text-muted-foreground">Загрузка...</p>
      </div>
    )
  }

  return (
    <div className="mt-6">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-medium">
          Объекты{objects && objects.length > 0 ? ` (${objects.length})` : ''}
        </h2>
        {canWrite && (
          <Button size="sm" variant="outline" onClick={() => setShowLink(true)}>
            + Привязать объект
          </Button>
        )}
      </div>

      {!objects || objects.length === 0 ? (
        <p className="text-sm text-muted-foreground">Объекты не привязаны</p>
      ) : (
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
                    <div className="flex justify-end items-center gap-2">
                      <Link href={`/dashboard/objects/${obj.id}`}>
                        <Button variant="ghost" size="sm">→</Button>
                      </Link>
                      {canWrite && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={unlink.isPending}
                          onClick={() => unlink.mutate(obj.id)}
                          className="text-destructive hover:text-destructive"
                        >
                          Отвязать
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showLink && (
        <LinkObjectModal
          contractId={contractId}
          linkedIds={linkedIds}
          onClose={() => setShowLink(false)}
        />
      )}
    </div>
  )
}
