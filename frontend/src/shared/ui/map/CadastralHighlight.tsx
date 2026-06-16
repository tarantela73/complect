'use client'

import { useEffect } from 'react'
import { CircleMarker, Polygon, useMap } from 'react-leaflet'
import type { LatLngExpression, LatLngTuple } from 'leaflet'

interface CadastralHighlightProps {
  geometry: GeoJSON.Geometry | null
  centerLat?: number
  centerLon?: number
}

const PANE_NAME = 'cadastralHighlight'
const PANE_Z_INDEX = 450

function geoJsonCoordsToLeaflet(coords: number[][]): LatLngTuple[] {
  return coords.map(([lon, lat]) => [lat, lon] as LatLngTuple)
}

export function CadastralHighlight({ geometry, centerLat, centerLon }: CadastralHighlightProps) {
  const map = useMap()

  useEffect(() => {
    if (!map.getPane(PANE_NAME)) {
      const pane = map.createPane(PANE_NAME)
      pane.style.zIndex = String(PANE_Z_INDEX)
    }
  }, [map])

  if (!geometry && centerLat == null) return null

  if (geometry?.type === 'Polygon') {
    const positions = geometry.coordinates.map((ring) =>
      geoJsonCoordsToLeaflet(ring as number[][]),
    ) as LatLngExpression[][]

    return (
      <Polygon
        positions={positions}
        pane={PANE_NAME}
        pathOptions={{
          color: '#f59e0b',
          weight: 2,
          dashArray: '6 4',
          fillColor: '#f59e0b',
          fillOpacity: 0.12,
        }}
      />
    )
  }

  if (geometry?.type === 'MultiPolygon') {
    const positions = (geometry.coordinates as number[][][][]).map((polygon) =>
      polygon.map((ring) => geoJsonCoordsToLeaflet(ring as number[][])),
    ) as LatLngExpression[][][]

    return (
      <Polygon
        positions={positions}
        pane={PANE_NAME}
        pathOptions={{
          color: '#f59e0b',
          weight: 2,
          dashArray: '6 4',
          fillColor: '#f59e0b',
          fillOpacity: 0.12,
        }}
      />
    )
  }

  // Fallback: только центроид без геометрии
  if (centerLat != null && centerLon != null) {
    return (
      <CircleMarker
        center={[centerLat, centerLon]}
        pane={PANE_NAME}
        radius={12}
        pathOptions={{
          color: '#f59e0b',
          weight: 3,
          dashArray: '4 2',
          fillColor: '#f59e0b',
          fillOpacity: 0.2,
        }}
      />
    )
  }

  return null
}
