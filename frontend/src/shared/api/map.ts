import { useQuery } from '@tanstack/react-query'
import { apiClient } from './client'
import { env } from '@/shared/config/env'

// Local types until SDK is regenerated after backend endpoint is added
export interface MapObjectResult {
  id: string
  name: string
  address: string
  lat: number | null
  lon: number | null
  status: string
}

export interface MapAddressResult {
  label: string
  lat: number
  lon: number
}

export interface MapCadastreResult {
  label: string
  center_lat: number
  center_lon: number
  geometry: GeoJSON.Geometry | null
}

export interface MapSearchResponse {
  objects: MapObjectResult[]
  addresses: MapAddressResult[]
  cadastre: MapCadastreResult[]
}

async function fetchMapSearch(q: string): Promise<MapSearchResponse> {
  const url = `${env.NEXT_PUBLIC_API_URL}/map/search?q=${encodeURIComponent(q)}`
  const resp = await fetch(url, { credentials: 'include' })
  if (!resp.ok) throw new Error(`map search ${resp.status}`)
  return resp.json()
}

export function useMapSearch(q: string) {
  return useQuery<MapSearchResponse>({
    queryKey: ['map-search', q],
    queryFn: () => fetchMapSearch(q),
    enabled: q.trim().length >= 3,
    staleTime: 60_000,
  })
}

export const EMPTY_SEARCH_RESPONSE: MapSearchResponse = {
  objects: [],
  addresses: [],
  cadastre: [],
}
