'use client'

import { useEffect, useRef } from 'react'
import { MapContainer, TileLayer, useMap } from 'react-leaflet'
import type { LatLngExpression } from 'leaflet'
import type { ObjectFeature, ObjectGeoJSON } from '@/shared/api/objects'
import type { TileProvider } from './tile-providers'
import { PKK_TILES, OPENINFRA_TILES } from './tile-providers'
import { ObjectMarker } from './ObjectMarker'
import type { FlyToTarget } from './MapFlyTo'
import { CadastralHighlight } from './CadastralHighlight'

const RUSSIA_CENTER: LatLngExpression = [55.75, 37.62]
const DEFAULT_ZOOM = 6

function FixLeafletIcons() {
  useEffect(() => {
    // Leaflet/webpack icon path fix
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const L = require('leaflet')
    delete (L.Icon.Default.prototype as Record<string, unknown>)._getIconUrl
    L.Icon.Default.mergeOptions({
      iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
      iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
      shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
    })
  }, [])
  return null
}

interface MapClickHandlerProps {
  onClick?: (lat: number, lng: number) => void
}

function MapClickHandler({ onClick }: MapClickHandlerProps) {
  const map = useMap()
  useEffect(() => {
    if (!onClick) return
    const handler = (e: import('leaflet').LeafletMouseEvent) => {
      onClick(e.latlng.lat, e.latlng.lng)
    }
    map.on('click', handler)
    return () => { map.off('click', handler) }
  }, [map, onClick])
  return null
}

export interface ClickedPoint {
  lat: number
  lon: number
  label: string | null
  loading: boolean
}

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string,
  )
}

function ClickPopup({ point }: { point: ClickedPoint | null }) {
  const map = useMap()
  useEffect(() => {
    if (!point) return
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const L = require('leaflet')
    const coords = `${point.lat.toFixed(6)}, ${point.lon.toFixed(6)}`
    const addr = point.loading
      ? 'Определение адреса…'
      : point.label
        ? escapeHtml(point.label)
        : 'Адрес не найден'
    const html =
      `<div style="font-size:12px;line-height:1.4;min-width:140px">` +
      `<div style="font-weight:500;margin-bottom:2px">${addr}</div>` +
      `<div style="color:#666;font-family:monospace">${coords}</div>` +
      `</div>`
    const popup = L.popup({ closeButton: true })
      .setLatLng([point.lat, point.lon])
      .setContent(html)
      .openOn(map)
    return () => {
      map.closePopup(popup)
    }
  }, [point, map])
  return null
}


interface MapViewProps {
  geoJSON?: ObjectGeoJSON
  features?: ObjectFeature[]
  provider: TileProvider
  center?: LatLngExpression
  zoom?: number
  height?: string
  showCadastral?: boolean
  showInfra?: boolean
  onCadastralUnavailable?: () => void
  flyTarget?: FlyToTarget | null
  highlightGeometry?: GeoJSON.Geometry | null
  highlightCenter?: { lat: number; lon: number } | null
  clickedPoint?: ClickedPoint | null
  onClick?: (lat: number, lng: number) => void
  children?: React.ReactNode
}

export function MapView({
  geoJSON,
  features: featuresProp,
  provider,
  center = RUSSIA_CENTER,
  zoom = DEFAULT_ZOOM,
  height = '100%',
  showCadastral = false,
  showInfra = false,
  flyTarget,
  highlightGeometry,
  highlightCenter,
  clickedPoint,
  onClick,
  onCadastralUnavailable,
  children,
}: MapViewProps) {
  const mapRef = useRef<import('leaflet').Map | null>(null)
  const cadastralErrorFiredRef = useRef(false)
  const features = featuresProp ?? geoJSON?.features ?? []
  const pointFeatures = features.filter((f) => f.geometry !== null && f.geometry !== undefined)

  // flyTo через mapRef — обходит цепочку prop→child→useEffect, которая ненадёжна
  // с dynamic() в Next.js 15 / React 19
  useEffect(() => {
    const map = mapRef.current
    if (!map || !flyTarget) return
    if (flyTarget.bounds) {
      map.fitBounds(flyTarget.bounds, { padding: [20, 20] })
    } else if (flyTarget.lat != null && flyTarget.lon != null) {
      map.flyTo([flyTarget.lat, flyTarget.lon], flyTarget.zoom ?? 16, { duration: 1 })
    }
  }, [flyTarget])

  const handleCadastralTileError = () => {
    if (!cadastralErrorFiredRef.current) {
      cadastralErrorFiredRef.current = true
      onCadastralUnavailable?.()
    }
  }

  return (
    <div style={{ height, width: '100%' }}>
      <MapContainer
        center={center}
        zoom={zoom}
        style={{ height: '100%', width: '100%' }}
        ref={mapRef}
      >
        <FixLeafletIcons />
        {onClick && <MapClickHandler onClick={onClick} />}

        <TileLayer
          url={provider.url}
          attribution={provider.attribution}
          maxZoom={provider.maxZoom ?? 19}
        />

        {showCadastral && (
          <TileLayer
            url={PKK_TILES.url}
            attribution={PKK_TILES.attribution}
            opacity={PKK_TILES.opacity}
            maxZoom={PKK_TILES.maxZoom}
            minZoom={PKK_TILES.minZoom}
            eventHandlers={{ tileerror: handleCadastralTileError }}
          />
        )}

        {showInfra && (
          <TileLayer
            url={OPENINFRA_TILES.url}
            attribution={OPENINFRA_TILES.attribution}
            opacity={OPENINFRA_TILES.opacity}
            {...(OPENINFRA_TILES.subdomains ? { subdomains: OPENINFRA_TILES.subdomains } : {})}
            maxZoom={OPENINFRA_TILES.maxZoom}
          />
        )}

        {pointFeatures.map((feature) => (
          <ObjectMarker key={String(feature.properties.id)} feature={feature} />
        ))}

        <CadastralHighlight
          geometry={highlightGeometry ?? null}
          centerLat={highlightCenter?.lat}
          centerLon={highlightCenter?.lon}
        />

        <ClickPopup point={clickedPoint ?? null} />

        {children}
      </MapContainer>
    </div>
  )
}
