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

const YANDEX: TileProvider = {
  id: 'yandex',
  label: 'Яндекс',
  url: 'https://core-sat.maps.yandex.net/tiles?l=sat&x={x}&y={y}&z={z}&scale=1&lang=ru_RU',
  attribution: '&copy; <a href="https://yandex.ru/maps">Яндекс.Карты</a>',
  maxZoom: 19,
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
  if (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_YANDEX_MAPS_API_KEY) {
    providers.push(YANDEX)
  } else if (typeof window !== 'undefined') {
    providers.push(YANDEX)
  }
  if (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_2GIS_API_KEY) {
    providers.push(GIS2)
  }
  return providers
}

export const ALL_PROVIDERS: TileProvider[] = [OSM, YANDEX, GIS2]

export const PKK_WMS = {
  url: 'https://pkk.rosreestr.ru/arcgis/rest/services/PKK6/CadastreSelected/MapServer/WMSServer',
  layers: '0',
  format: 'image/png',
  transparent: true,
  version: '1.1.1',
  attribution: '&copy; Росреестр',
}

export const OPENINFRA_WMS = {
  url: 'https://openinframap.org/tiles/{z}/{x}/{y}.png',
  attribution: '&copy; <a href="https://openinframap.org">OpenInfraMap</a>',
}
