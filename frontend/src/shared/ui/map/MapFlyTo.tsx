'use client'

import { useEffect } from 'react'
import { useMap } from 'react-leaflet'
import type { LatLngBoundsExpression } from 'leaflet'

export interface FlyToTarget {
  lat?: number
  lon?: number
  zoom?: number
  bounds?: LatLngBoundsExpression
}

interface MapFlyToProps {
  target: FlyToTarget | null
}

export function MapFlyTo({ target }: MapFlyToProps) {
  const map = useMap()

  useEffect(() => {
    if (!target) return
    if (target.bounds) {
      map.fitBounds(target.bounds, { padding: [20, 20] })
    } else if (target.lat != null && target.lon != null) {
      map.flyTo([target.lat, target.lon], target.zoom ?? 16, { duration: 1 })
    }
  }, [map, target])

  return null
}
