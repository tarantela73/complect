'use client'

import { useState } from 'react'
import dynamic from 'next/dynamic'
import { Marker } from 'react-leaflet'
import type { LatLngExpression } from 'leaflet'
import { useUpdateObjectLocation } from '@/shared/api/objects'
import { useCan } from '@/shared/auth/hooks'
import { Button } from '@/shared/ui/button'
import { ALL_PROVIDERS } from '@/shared/ui/map/tile-providers'

const MapView = dynamic(() => import('@/shared/ui/map/MapView').then((m) => m.MapView), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
      Загрузка карты...
    </div>
  ),
})

interface MapPickerModalProps {
  objectId: string
  initialCenter?: LatLngExpression
  onClose: () => void
}

function MapPickerModal({ objectId, initialCenter, onClose }: MapPickerModalProps) {
  const [picked, setPicked] = useState<{ lat: number; lng: number } | null>(null)
  const update = useUpdateObjectLocation(objectId)
  const provider = ALL_PROVIDERS[0]

  const handleSave = async () => {
    if (!picked) return
    try {
      await update.mutateAsync({ longitude: picked.lng, latitude: picked.lat })
      onClose()
    } catch {
      // error shown via button disabled state
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-2xl rounded-lg bg-background p-4 shadow-xl flex flex-col gap-3">
        <h2 className="text-base font-semibold">Установить точку на карте</h2>
        <p className="text-xs text-muted-foreground">Кликните на карту, чтобы выбрать место объекта</p>

        <div className="rounded-md overflow-hidden border border-border" style={{ height: 400 }}>
          <MapView
            provider={provider}
            center={initialCenter ?? [55.75, 37.62]}
            zoom={initialCenter ? 14 : 6}
            height="400px"
            onClick={(lat, lng) => setPicked({ lat, lng })}
          >
            {picked && (
              <Marker position={[picked.lat, picked.lng]} />
            )}
          </MapView>
        </div>

        {picked && (
          <p className="text-xs text-muted-foreground">
            Выбрано: {picked.lat.toFixed(6)}, {picked.lng.toFixed(6)}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>Отмена</Button>
          <Button
            size="sm"
            disabled={!picked || update.isPending}
            onClick={handleSave}
          >
            {update.isPending ? 'Сохранение...' : 'Сохранить'}
          </Button>
        </div>
      </div>
    </div>
  )
}

interface LocationSectionProps {
  objectId: string
  longitude?: number | null
  latitude?: number | null
}

export function LocationSection({ objectId, longitude, latitude }: LocationSectionProps) {
  const canWrite = useCan('objects.write')
  const [showPicker, setShowPicker] = useState(false)
  const provider = ALL_PROVIDERS[0]

  const hasLocation = longitude !== null && longitude !== undefined &&
                      latitude !== null && latitude !== undefined

  const center: LatLngExpression | undefined = hasLocation
    ? [latitude as number, longitude as number]
    : undefined

  return (
    <div className="mt-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-muted-foreground">Местоположение</span>
        {canWrite && (
          <Button variant="outline" size="sm" onClick={() => setShowPicker(true)}>
            {hasLocation ? 'Изменить точку' : 'Установить точку'}
          </Button>
        )}
      </div>

      {hasLocation ? (
        <div>
          <div className="rounded-md overflow-hidden border border-border" style={{ height: 220 }}>
            <MapView
              provider={provider}
              center={center}
              zoom={14}
              height="220px"
            />
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            {(latitude as number).toFixed(6)}, {(longitude as number).toFixed(6)}
          </p>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Координаты не установлены</p>
      )}

      {showPicker && (
        <MapPickerModal
          objectId={objectId}
          initialCenter={center}
          onClose={() => setShowPicker(false)}
        />
      )}
    </div>
  )
}
