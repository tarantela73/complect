import { notFound } from 'next/navigation'
import { TiptapRenderer } from '@/shared/ui/public/TiptapRenderer'
import { LeadForm } from '../../components/LeadForm'

const SERVICE_TITLES: Record<string, string> = {
  electricity: 'Электроснабжение',
  gas: 'Газоснабжение',
  water: 'Водоснабжение',
  heat: 'Теплоснабжение',
  sewage: 'Водоотведение',
  construction: 'Строительство',
}

async function getCmsContent(slug: string): Promise<Record<string, unknown>> {
  try {
    const res = await fetch(
      `${process.env.NEXT_PUBLIC_API_URL ?? 'http://backend:8000'}/api/v1/public/cms/services-${slug}/`,
      { cache: 'no-store' },
    )
    if (!res.ok) return {}
    const data = await res.json()
    return (data.content as Record<string, unknown>) ?? {}
  } catch {
    return {}
  }
}

export async function generateStaticParams() {
  return Object.keys(SERVICE_TITLES).map((slug) => ({ slug }))
}

export default async function ServicePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params
  const title = SERVICE_TITLES[slug]
  if (!title) notFound()

  const content = await getCmsContent(slug)

  return (
    <div>
      <section className="bg-muted/40 py-16">
        <div className="mx-auto max-w-6xl px-4">
          <h1 className="mb-4 text-4xl font-bold">{title}</h1>
          <p className="text-lg text-muted-foreground">
            Профессиональное подключение и полное сопровождение проекта
          </p>
        </div>
      </section>

      <section className="py-16">
        <div className="mx-auto max-w-4xl px-4">
          {content.body ? (
            <TiptapRenderer
              content={content.body as Record<string, unknown>}
              className="prose prose-lg max-w-none"
            />
          ) : (
            <div className="prose prose-lg max-w-none text-muted-foreground">
              <p>
                Мы предоставляем полный цикл работ по услуге «{title}»: от получения технических
                условий до сдачи объекта в эксплуатацию. Работаем с юридическими и физическими лицами.
              </p>
            </div>
          )}
        </div>
      </section>

      <section className="bg-muted/40 py-16">
        <div className="mx-auto max-w-xl px-4">
          <h2 className="mb-8 text-center text-2xl font-bold">Оставить заявку</h2>
          <LeadForm />
        </div>
      </section>
    </div>
  )
}
