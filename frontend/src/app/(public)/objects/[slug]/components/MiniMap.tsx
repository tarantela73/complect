'use client'

import { useEffect } from 'react'
import { MapContainer, TileLayer, Marker, useMap } from 'react-leaflet'

function FixLeafletIcons() {
  useEffect(() => {
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

function Attribution() {
  const map = useMap()
  useEffect(() => {
    map.attributionControl.setPrefix('')
  }, [map])
  return null
}

interface MiniMapProps {
  lat: number
  lon: number
}

export function MiniMap({ lat, lon }: MiniMapProps) {
  return (
    <div className="h-64 w-full overflow-hidden rounded-md border border-border">
      <MapContainer
        center={[lat, lon]}
        zoom={14}
        style={{ height: '100%', width: '100%' }}
        zoomControl={false}
        scrollWheelZoom={false}
        dragging={false}
      >
        <FixLeafletIcons />
        <Attribution />
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          maxZoom={19}
        />
        <Marker position={[lat, lon]} />
      </MapContainer>
    </div>
  )
}
