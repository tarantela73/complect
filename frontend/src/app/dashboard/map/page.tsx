'use client'

import dynamic from 'next/dynamic'
import { useState } from 'react'
import { useObjectsGeoJSON, RESOURCE_TYPE_LABELS, OBJECT_STATUS_LABELS } from '@/shared/api/objects'
import { ALL_PROVIDERS } from '@/shared/ui/map/tile-providers'
import type { TileProvider } from '@/shared/ui/map/tile-providers'

const MapView = dynamic(() => import('@/shared/ui/map/MapView').then((m) => m.MapView), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
      Загрузка карты...
    </div>
  ),
})

const RESOURCE_TYPES = Object.entries(RESOURCE_TYPE_LABELS)
const OBJECT_STATUSES = Object.entries(OBJECT_STATUS_LABELS)

export default function MapPage() {
  const [resourceType, setResourceType] = useState('')
  const [status, setStatus] = useState('')
  const [providerId, setProviderId] = useState('osm')
  const [showCadastral, setShowCadastral] = useState(false)
  const [showInfra, setShowInfra] = useState(false)

  const { data: geoJSON, isLoading, isError } = useObjectsGeoJSON()

  const provider = ALL_PROVIDERS.find((p) => p.id === providerId) ?? ALL_PROVIDERS[0]

  const filteredFeatures = geoJSON?.features.filter((f) => {
    if (resourceType && f.properties.resource_type !== resourceType) return false
    if (status && f.properties.status !== status) return false
    return true
  }) ?? []

  const withCoords = filteredFeatures.filter((f) => f.geometry !== null && f.geometry !== undefined)
  const total = filteredFeatures.length

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)]">
      <div className="flex items-center gap-3 px-4 py-2 border-b border-border bg-background shrink-0 flex-wrap">
        <span className="text-sm font-medium">Карта объектов</span>

        <select
          value={resourceType}
          onChange={(e) => setResourceType(e.target.value)}
          className="text-xs border border-input rounded-md px-2 py-1 bg-background"
        >
          <option value="">Все ресурсы</option>
          {RESOURCE_TYPES.map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>

        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="text-xs border border-input rounded-md px-2 py-1 bg-background"
        >
          <option value="">Все статусы</option>
          {OBJECT_STATUSES.map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>

        <select
          value={providerId}
          onChange={(e) => setProviderId(e.target.value)}
          className="text-xs border border-input rounded-md px-2 py-1 bg-background"
        >
          {ALL_PROVIDERS.map((p) => (
            <option key={p.id} value={p.id}>{p.label}</option>
          ))}
        </select>

        <label className="flex items-center gap-1 text-xs cursor-pointer">
          <input
            type="checkbox"
            checked={showCadastral}
            onChange={(e) => setShowCadastral(e.target.checked)}
            className="rounded"
          />
          Кадастр
        </label>

        <label className="flex items-center gap-1 text-xs cursor-pointer">
          <input
            type="checkbox"
            checked={showInfra}
            onChange={(e) => setShowInfra(e.target.checked)}
            className="rounded"
          />
          Коммуникации
        </label>

        <span className="ml-auto text-xs text-muted-foreground">
          {isLoading
            ? 'Загрузка...'
            : isError
            ? 'Ошибка загрузки'
            : `${withCoords.length} из ${total} объектов на карте`}
        </span>
      </div>

      <div className="flex-1 min-h-0">
        {isError ? (
          <div className="flex items-center justify-center h-full text-sm text-destructive">
            Ошибка загрузки данных
          </div>
        ) : (
          <MapView
            features={filteredFeatures}
            provider={provider}
            showCadastral={showCadastral}
            showInfra={showInfra}
            height="100%"
          />
        )}
      </div>

      <div className="flex items-center gap-3 px-4 py-1.5 border-t border-border bg-background shrink-0 flex-wrap">
        {[
          { label: 'Новый', color: '#6b7280' },
          { label: 'В работе', color: '#3b82f6' },
          { label: 'Приостановлен', color: '#f59e0b' },
          { label: 'Завершён', color: '#22c55e' },
          { label: 'Отменён', color: '#ef4444' },
        ].map(({ label, color }) => (
          <div key={label} className="flex items-center gap-1 text-xs">
            <span
              className="inline-block w-3 h-3 rounded-full"
              style={{ backgroundColor: color }}
            />
            {label}
          </div>
        ))}
      </div>
    </div>
  )
}
