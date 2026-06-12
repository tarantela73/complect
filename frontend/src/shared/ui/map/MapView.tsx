'use client'

import { useEffect, useRef } from 'react'
import { MapContainer, TileLayer, WMSTileLayer, LayersControl, useMap } from 'react-leaflet'
import type { LatLngExpression } from 'leaflet'
import type { ObjectFeature, ObjectGeoJSON } from '@/shared/api/objects'
import type { TileProvider } from './tile-providers'
import { PKK_WMS } from './tile-providers'
import { ObjectMarker } from './ObjectMarker'

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

interface MapViewProps {
  geoJSON?: ObjectGeoJSON
  features?: ObjectFeature[]
  provider: TileProvider
  center?: LatLngExpression
  zoom?: number
  height?: string
  showCadastral?: boolean
  showInfra?: boolean
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
  onClick,
  children,
}: MapViewProps) {
  const mapRef = useRef<import('leaflet').Map | null>(null)
  const features = featuresProp ?? geoJSON?.features ?? []
  const pointFeatures = features.filter((f) => f.geometry !== null && f.geometry !== undefined)

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

        <LayersControl position="topright">
          <LayersControl.BaseLayer checked name={provider.label}>
            <TileLayer
              url={provider.url}
              attribution={provider.attribution}
              maxZoom={provider.maxZoom ?? 19}
            />
          </LayersControl.BaseLayer>

          <LayersControl.Overlay checked={showCadastral} name="Кадастр (Росреестр)">
            <WMSTileLayer
              url={PKK_WMS.url}
              layers={PKK_WMS.layers}
              format={PKK_WMS.format}
              transparent={PKK_WMS.transparent}
              version={PKK_WMS.version}
              attribution={PKK_WMS.attribution}
              opacity={0.7}
            />
          </LayersControl.Overlay>

          <LayersControl.Overlay checked={showInfra} name="Коммуникации (OSM)">
            <TileLayer
              url="https://openinframap.org/tiles/{z}/{x}/{y}.png"
              attribution='&copy; <a href="https://openinframap.org">OpenInfraMap</a>'
              opacity={0.75}
            />
          </LayersControl.Overlay>
        </LayersControl>

        {pointFeatures.map((feature) => (
          <ObjectMarker key={String(feature.properties.id)} feature={feature} />
        ))}

        {children}
      </MapContainer>
    </div>
  )
}
