export interface TileProvider {
  id: string
  label: string
  url: string
  attribution: string
  maxZoom?: number
  subdomains?: string[]
}

const OSM: TileProvider = {
  id: 'osm',
  label: 'OpenStreetMap',
  url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  maxZoom: 19,
}

// Google Hybrid: satellite + Cyrillic labels/roads in one tile request.
// Unofficial tile access — violates Google Maps ToS, but stable for internal tools.
const GOOGLE_HYBRID: TileProvider = {
  id: 'google-hybrid',
  label: 'Google (гибрид)',
  url: 'https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
  attribution: '&copy; <a href="https://maps.google.com">Google Maps</a>',
  maxZoom: 20,
}

// WGS84-accurate satellite, no CRS offset, free for limited use.
const ESRI: TileProvider = {
  id: 'esri',
  label: 'Спутник (ESRI)',
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  attribution: '&copy; <a href="https://www.esri.com">Esri</a>, DigitalGlobe, GeoEye, USGS',
  maxZoom: 18,
}

const GIS2: TileProvider = {
  id: '2gis',
  label: '2ГИС',
  url: 'https://tile2.maps.2gis.com/tiles?x={x}&y={y}&z={z}&v=1&ts=online_hd',
  attribution: '&copy; <a href="https://2gis.ru">2ГИС</a>',
  maxZoom: 18,
}

export function getAvailableProviders(): TileProvider[] {
  const providers: TileProvider[] = [OSM]
  if (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_2GIS_API_KEY) {
    providers.push(GIS2)
  }
  providers.push(GOOGLE_HYBRID, ESRI)
  return providers
}

export const ALL_PROVIDERS: TileProvider[] = [OSM, GIS2, GOOGLE_HYBRID, ESRI]

// 2026-06-17: nspd.gov.ru returns SPA HTML (Content-Type: text/html, 15 KB) for ALL
// /arcgis/rest/services/PKK6/.../tile/* paths — no ArcGIS tile server is exposed.
// SSL cert is self-signed (blocked by browsers). No public alternative exists.
// Flip MAP_CADASTRAL_AVAILABLE to true when a working tile endpoint is confirmed.
export const MAP_CADASTRAL_AVAILABLE = false

export const PKK_TILES = {
  url: 'https://nspd.gov.ru/arcgis/rest/services/PKK6/CadastreObjects/MapServer/tile/{z}/{y}/{x}',
  attribution: '&copy; <a href="https://nspd.gov.ru">Росреестр НСПД</a>',
  maxZoom: 20,
  minZoom: 5,
  opacity: 0.7,
}

