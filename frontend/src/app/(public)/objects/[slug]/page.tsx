import { notFound } from 'next/navigation'
import Link from 'next/link'
import { LeadForm } from '../../components/LeadForm'
import { ClientMiniMap } from './components/ClientMiniMap'

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

async function fetchObject(slug: string): Promise<PublicObject | null> {
  try {
    const res = await fetch(
      `${API_URL}/api/v1/public/objects/${slug}/`,
      { cache: 'no-store' },
    )
    if (res.status === 404) return null
    if (!res.ok) return null
    return res.json()
  } catch {
    return null
  }
}

interface PageProps {
  params: Promise<{ slug: string }>
}

export default async function ObjectDetailPage({ params }: PageProps) {
  const { slug } = await params
  const obj = await fetchObject(slug)

  if (!obj) notFound()

  return (
    <div className="mx-auto max-w-4xl px-4 py-16">
      <nav className="mb-8 text-sm text-muted-foreground">
        <Link href="/" className="hover:text-foreground">Главная</Link>
        {' / '}
        <Link href="/objects" className="hover:text-foreground">Наши объекты</Link>
        {' / '}
        <span className="text-foreground">{obj.name}</span>
      </nav>

      <div className="mb-2 text-sm font-medium text-primary">
        {RESOURCE_TYPE_LABELS[obj.resource_type] ?? obj.resource_type}
      </div>

      <h1 className="mb-4 text-4xl font-bold">{obj.name}</h1>

      {obj.address && (
        <p className="mb-8 text-lg text-muted-foreground">{obj.address}</p>
      )}

      {obj.cover_image_url && (
        <div className="mb-10 overflow-hidden rounded-lg">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={obj.cover_image_url}
            alt={obj.name}
            className="w-full object-cover"
            style={{ maxHeight: '400px' }}
          />
        </div>
      )}

      {obj.description && (
        <div className="mb-10">
          <h2 className="mb-3 text-xl font-semibold">Описание</h2>
          <p className="text-muted-foreground leading-relaxed">{obj.description}</p>
        </div>
      )}

      {obj.location && (
        <div className="mb-10">
          <h2 className="mb-3 text-xl font-semibold">Местоположение</h2>
          <ClientMiniMap lat={obj.location.lat} lon={obj.location.lon} />
        </div>
      )}

      <div className="mt-12 rounded-lg border border-border p-8">
        <h2 className="mb-2 text-2xl font-semibold">Оставить заявку</h2>
        <p className="mb-6 text-muted-foreground">
          Свяжитесь с нами, чтобы узнать подробнее об объекте «{obj.name}»
        </p>
        <LeadForm />
      </div>
    </div>
  )
}
