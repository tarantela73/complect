import Link from 'next/link'
import { cn } from '@/shared/ui/utils'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://backend:8000'

const RESOURCE_TYPE_LABELS: Record<string, string> = {
  GAS: 'Газоснабжение',
  WATER: 'Водоснабжение',
  ELECTRICITY: 'Электроснабжение',
  HEAT: 'Теплоснабжение',
  SEWAGE: 'Водоотведение',
  OTHER: 'Прочее',
}

interface PublicObject {
  id: string
  slug: string
  name: string
  address: string
  resource_type: string
  object_type: string
  description: string
  cover_image_url: string | null
  location: { lat: number; lon: number } | null
}

interface ObjectsResponse {
  results: PublicObject[]
  next_cursor: string | null
  count: number
}

async function fetchObjects(resourceType?: string, cursor?: string): Promise<ObjectsResponse> {
  const params = new URLSearchParams()
  if (resourceType) params.set('resource_type', resourceType)
  if (cursor) params.set('cursor', cursor)
  params.set('limit', '20')

  try {
    const res = await fetch(
      `${API_URL}/api/v1/public/objects/?${params.toString()}`,
      { cache: 'no-store' },
    )
    if (!res.ok) return { results: [], next_cursor: null, count: 0 }
    return res.json()
  } catch {
    return { results: [], next_cursor: null, count: 0 }
  }
}

interface PageProps {
  searchParams: Promise<{ resource_type?: string; cursor?: string }>
}

export default async function ObjectsPage({ searchParams }: PageProps) {
  const { resource_type, cursor } = await searchParams
  const data = await fetchObjects(resource_type, cursor)

  return (
    <div className="mx-auto max-w-6xl px-4 py-16">
      <h1 className="mb-4 text-4xl font-bold">Наши объекты</h1>
      <p className="mb-10 text-lg text-muted-foreground">
        Реализованные и текущие проекты подключения к инженерным сетям
      </p>

      {/* Filter chips */}
      <div className="mb-8 flex flex-wrap gap-2">
        <Link
          href="/objects"
          className={cn(
            'rounded-full border px-4 py-1.5 text-sm font-medium transition-colors',
            !resource_type
              ? 'border-primary bg-primary text-primary-foreground'
              : 'border-border hover:border-primary hover:text-primary',
          )}
        >
          Все типы
        </Link>
        {Object.entries(RESOURCE_TYPE_LABELS).map(([key, label]) => (
          <Link
            key={key}
            href={`/objects?resource_type=${key}`}
            className={cn(
              'rounded-full border px-4 py-1.5 text-sm font-medium transition-colors',
              resource_type === key
                ? 'border-primary bg-primary text-primary-foreground'
                : 'border-border hover:border-primary hover:text-primary',
            )}
          >
            {label}
          </Link>
        ))}
      </div>

      {data.results.length === 0 ? (
        <div className="py-24 text-center text-muted-foreground">
          <p className="text-lg">Объекты не найдены</p>
          {resource_type && (
            <Link href="/objects" className="mt-4 inline-block text-sm text-primary hover:underline">
              Показать все объекты
            </Link>
          )}
        </div>
      ) : (
        <>
          <p className="mb-6 text-sm text-muted-foreground">Найдено: {data.count}</p>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {data.results.map((obj) => (
              <ObjectCard key={obj.id} obj={obj} />
            ))}
          </div>

          {data.next_cursor && (
            <div className="mt-10 text-center">
              <Link
                href={`/objects?${resource_type ? `resource_type=${resource_type}&` : ''}cursor=${data.next_cursor}`}
                className="rounded-md border border-border px-6 py-2 text-sm font-medium hover:border-primary hover:text-primary transition-colors"
              >
                Показать ещё
              </Link>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function ObjectCard({ obj }: { obj: PublicObject }) {
  return (
    <Link
      href={`/objects/${obj.slug}`}
      className="group flex flex-col overflow-hidden rounded-lg border border-border transition-shadow hover:shadow-md"
    >
      <div className="flex h-40 items-center justify-center bg-muted">
        {obj.cover_image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={obj.cover_image_url}
            alt={obj.name}
            className="h-full w-full object-cover"
          />
        ) : (
          <span className="text-4xl text-muted-foreground/30">🏗</span>
        )}
      </div>
      <div className="flex flex-1 flex-col p-4">
        <div className="mb-1 text-xs font-medium text-primary">
          {RESOURCE_TYPE_LABELS[obj.resource_type] ?? obj.resource_type}
        </div>
        <h2 className="mb-1 font-semibold leading-tight group-hover:text-primary transition-colors">
          {obj.name}
        </h2>
        {obj.address && (
          <p className="mt-auto pt-2 text-xs text-muted-foreground truncate">{obj.address}</p>
        )}
      </div>
    </Link>
  )
}
