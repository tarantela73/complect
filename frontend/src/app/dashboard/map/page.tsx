'use client'

import dynamic from 'next/dynamic'
import { useRouter } from 'next/navigation'
import { useCallback, useState } from 'react'
import { useObjectsGeoJSON, RESOURCE_TYPE_LABELS, OBJECT_STATUS_LABELS } from '@/shared/api/objects'
import { ALL_PROVIDERS, MAP_CADASTRAL_AVAILABLE } from '@/shared/ui/map/tile-providers'
import type { TileProvider } from '@/shared/ui/map/tile-providers'
import { MapSearchBar } from '@/shared/ui/map/MapSearchBar'
import type { SearchResult } from '@/shared/ui/map/MapSearchBar'
import type { FlyToTarget } from '@/shared/ui/map/MapFlyTo'
import type { ClickedPoint } from '@/shared/ui/map/MapView'
import { reverseGeocode } from '@/shared/api/map'

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
  const router = useRouter()
  const [resourceType, setResourceType] = useState('')
  const [status, setStatus] = useState('')
  const [providerId, setProviderId] = useState('osm')
  const [showCadastral, setShowCadastral] = useState(false)

  // Search state
  const [flyTarget, setFlyTarget] = useState<FlyToTarget | null>(null)
  const [highlightGeometry, setHighlightGeometry] = useState<GeoJSON.Geometry | null>(null)
  const [highlightCenter, setHighlightCenter] = useState<{ lat: number; lon: number } | null>(null)
  const [clickedPoint, setClickedPoint] = useState<ClickedPoint | null>(null)
  const [cadastralUnavailable, setCadastralUnavailable] = useState(false)

  const handleCadastralUnavailable = useCallback(() => {
    setCadastralUnavailable(true)
  }, [])

  const { data: geoJSON, isLoading, isError } = useObjectsGeoJSON()

  const provider = ALL_PROVIDERS.find((p) => p.id === providerId) ?? ALL_PROVIDERS[0]

  const filteredFeatures = geoJSON?.features.filter((f) => {
    if (resourceType && f.properties.resource_type !== resourceType) return false
    if (status && f.properties.status !== status) return false
    return true
  }) ?? []

  const withCoords = filteredFeatures.filter((f) => f.geometry !== null && f.geometry !== undefined)
  const total = filteredFeatures.length

  const handleSearchResult = useCallback(
    (result: SearchResult) => {
      setClickedPoint(null)
      if (result.type === 'address') {
        setHighlightGeometry(null)
        setHighlightCenter({ lat: result.data.lat, lon: result.data.lon })
        setFlyTarget({ lat: result.data.lat, lon: result.data.lon, zoom: 16 })
      } else if (result.type === 'cadastre') {
        const cad = result.data
        setHighlightGeometry(cad.geometry)
        setHighlightCenter({ lat: cad.center_lat, lon: cad.center_lon })
        setFlyTarget({ lat: cad.center_lat, lon: cad.center_lon, zoom: 17 })
      } else if (result.type === 'object') {
        if (result.data.lat != null && result.data.lon != null) {
          setHighlightGeometry(null)
          setHighlightCenter({ lat: result.data.lat, lon: result.data.lon })
          setFlyTarget({ lat: result.data.lat, lon: result.data.lon, zoom: 17 })
        } else {
          router.push(`/dashboard/objects/${result.data.id}`)
        }
      }
    },
    [router],
  )

  const handleMapClick = useCallback(async (lat: number, lon: number) => {
    setHighlightGeometry(null)
    setHighlightCenter(null)
    setClickedPoint({ lat, lon, label: null, loading: true })
    try {
      const r = await reverseGeocode(lat, lon)
      setClickedPoint({ lat, lon, label: r.label, loading: false })
    } catch {
      setClickedPoint({ lat, lon, label: null, loading: false })
    }
  }, [])

  const handleGeolocate = useCallback(() => {
    if (!navigator.geolocation) return
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setFlyTarget({ lat: pos.coords.latitude, lon: pos.coords.longitude, zoom: 15 })
      },
      () => {
        // silently ignore if user denies
      },
    )
  }, [])

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)]">
      <div className="relative z-20 flex items-center gap-3 px-4 py-2 border-b border-border bg-background shrink-0 flex-wrap">
        <span className="text-sm font-medium">Карта объектов</span>

        <MapSearchBar onResult={handleSearchResult} />

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

        <label
          className={`flex items-center gap-1 text-xs ${MAP_CADASTRAL_AVAILABLE ? 'cursor-pointer' : 'cursor-not-allowed opacity-50'}`}
          title={
            !MAP_CADASTRAL_AVAILABLE
              ? 'Кадастровые тайлы временно недоступны — НСПД Росреестра не предоставляет публичный тайловый сервис'
              : cadastralUnavailable
                ? 'Сервис ПКК Росреестра недоступен'
                : 'Кадастровые границы (ПКК Росреестра)'
          }
        >
          <input
            type="checkbox"
            checked={showCadastral}
            onChange={(e) => { setShowCadastral(e.target.checked); setCadastralUnavailable(false) }}
            className="rounded"
            disabled={!MAP_CADASTRAL_AVAILABLE}
          />
          Кадастр{cadastralUnavailable && <span className="text-destructive ml-0.5">(!)</span>}
        </label>

        <button
          type="button"
          onClick={handleGeolocate}
          title="Моё местоположение"
          className="text-xs border border-input rounded-md px-2 py-1 bg-background hover:bg-accent transition-colors"
        >
          📍 Моё место
        </button>

        <span className="ml-auto text-xs text-muted-foreground">
          {isLoading
            ? 'Загрузка...'
            : isError
            ? 'Ошибка загрузки'
            : `${withCoords.length} из ${total} объектов на карте`}
        </span>
      </div>

      <div className="relative z-0 flex-1 min-h-0">
        {isError ? (
          <div className="flex items-center justify-center h-full text-sm text-destructive">
            Ошибка загрузки данных
          </div>
        ) : (
          <MapView
            features={filteredFeatures}
            provider={provider}
            showCadastral={showCadastral}
            flyTarget={flyTarget}
            highlightGeometry={highlightGeometry}
            highlightCenter={highlightCenter}
            clickedPoint={clickedPoint}
            onClick={handleMapClick}
            onCadastralUnavailable={handleCadastralUnavailable}
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
