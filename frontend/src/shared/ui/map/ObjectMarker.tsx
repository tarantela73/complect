'use client'

import { CircleMarker, Popup } from 'react-leaflet'
import Link from 'next/link'
import type { ObjectFeature } from '@/shared/api/objects'
import { OBJECT_STATUS_LABELS } from '@/shared/api/objects'

const STATUS_COLORS: Record<string, string> = {
  NEW: '#6b7280',
  IN_PROGRESS: '#3b82f6',
  PAUSED: '#f59e0b',
  COMPLETED: '#22c55e',
  CANCELLED: '#ef4444',
}

interface ObjectMarkerProps {
  feature: ObjectFeature
}

export function ObjectMarker({ feature }: ObjectMarkerProps) {
  if (!feature.geometry) return null

  const [lng, lat] = feature.geometry.coordinates
  const { id, name, address, status } = feature.properties
  const color = STATUS_COLORS[status] ?? '#6b7280'

  return (
    <CircleMarker
      center={[lat, lng]}
      radius={8}
      pathOptions={{
        color: color,
        fillColor: color,
        fillOpacity: 0.85,
        weight: 2,
      }}
    >
      <Popup>
        <div className="min-w-[180px]">
          <div className="font-semibold text-sm mb-1">{name}</div>
          {address && <div className="text-xs text-gray-500 mb-1">{address}</div>}
          <div className="text-xs mb-2">
            <span
              className="inline-block rounded-full px-2 py-0.5 text-white text-xs"
              style={{ backgroundColor: color }}
            >
              {OBJECT_STATUS_LABELS[status] ?? status}
            </span>
          </div>
          <Link
            href={`/dashboard/objects/${id}`}
            className="text-xs text-blue-600 hover:underline"
          >
            Открыть →
          </Link>
        </div>
      </Popup>
    </CircleMarker>
  )
}
