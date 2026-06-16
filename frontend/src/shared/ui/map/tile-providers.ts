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

// ArcGIS REST tile URL format: {z}/{y}/{x} = level/row/col (matches Leaflet XYZ scheme)
export const PKK_TILES = {
  url: 'https://pkk.rosreestr.ru/arcgis/rest/services/PKK6/CadastreObjects/MapServer/tile/{z}/{y}/{x}',
  attribution: '&copy; <a href="https://rosreestr.gov.ru">Росреестр ПКК</a>',
  maxZoom: 20,
  minZoom: 5,
  opacity: 0.7,
}

// MAP_ZOUIT_ENABLED: true = Росреестр PKK6 ЗОУИТ охранные зоны (ЛЭП, газ, водоканал, связь)
// false = fallback к OpenRailwayMap (ж/д коридоры). Toggle при недоступности сервиса Росреестра.
export const MAP_ZOUIT_ENABLED = true

export const OPENINFRA_TILES = MAP_ZOUIT_ENABLED
  ? {
      url: 'https://pkk.rosreestr.ru/arcgis/rest/services/PKK6/ZuitsPolygons/MapServer/tile/{z}/{y}/{x}',
      attribution: '&copy; <a href="https://rosreestr.gov.ru">Росреестр ЗОУИТ</a>',
      maxZoom: 18,
      opacity: 0.65,
      subdomains: undefined as string | undefined,
    }
  : {
      url: 'https://{s}.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png',
      attribution:
        '&copy; <a href="https://www.openrailwaymap.org">OpenRailwayMap</a> | &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      subdomains: 'abc' as string | undefined,
      maxZoom: 19,
      opacity: 0.75,
    }
