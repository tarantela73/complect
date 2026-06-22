'use client'

import dynamic from 'next/dynamic'

const MiniMapDynamic = dynamic(
  () => import('./MiniMap').then((m) => m.MiniMap),
  { ssr: false },
)

interface ClientMiniMapProps {
  lat: number
  lon: number
}

export function ClientMiniMap({ lat, lon }: ClientMiniMapProps) {
  return <MiniMapDynamic lat={lat} lon={lon} />
}
