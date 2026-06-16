import { TiptapRenderer } from '@/shared/ui/public/TiptapRenderer'
import { LeadForm } from '../components/LeadForm'

async function getCmsContent(slug: string): Promise<Record<string, unknown>> {
  try {
    const res = await fetch(
      `${process.env.NEXT_PUBLIC_API_URL ?? 'http://backend:8000'}/api/v1/public/cms/${slug}/`,
      { cache: 'no-store' },
    )
    if (!res.ok) return {}
    const data = await res.json()
    return (data.content as Record<string, unknown>) ?? {}
  } catch {
    return {}
  }
}

export default async function ContactsPage() {
  const content = await getCmsContent('contacts')

  return (
    <div className="mx-auto max-w-6xl px-4 py-16">
      <h1 className="mb-12 text-4xl font-bold">Контакты</h1>

      <div className="grid gap-12 lg:grid-cols-2">
        <div>
          {content.body ? (
            <TiptapRenderer
              content={content.body as Record<string, unknown>}
              className="prose prose-lg max-w-none"
            />
          ) : (
            <div className="space-y-4 text-muted-foreground">
              <div>
                <p className="font-semibold text-foreground">Адрес</p>
                <p>г. Москва, ул. Примерная, д. 1, офис 100</p>
              </div>
              <div>
                <p className="font-semibold text-foreground">Телефон</p>
                <p>+7 (495) 000-00-00</p>
              </div>
              <div>
                <p className="font-semibold text-foreground">Email</p>
                <p>info@complect.ru</p>
              </div>
              <div>
                <p className="font-semibold text-foreground">Режим работы</p>
                <p>Пн–Пт: 9:00–18:00</p>
              </div>
            </div>
          )}
        </div>

        <div>
          <h2 className="mb-6 text-2xl font-semibold">Написать нам</h2>
          <LeadForm />
        </div>
      </div>
    </div>
  )
}
