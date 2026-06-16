import { TiptapRenderer } from '@/shared/ui/public/TiptapRenderer'

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

export default async function AboutPage() {
  const content = await getCmsContent('about')
  const meta = (content.meta as Record<string, string>) ?? {}

  return (
    <div className="mx-auto max-w-4xl px-4 py-16">
      <h1 className="mb-8 text-4xl font-bold">
        {meta.title ?? 'О компании'}
      </h1>

      {content.body ? (
        <TiptapRenderer
          content={content.body as Record<string, unknown>}
          className="prose prose-lg max-w-none"
        />
      ) : (
        <div className="prose prose-lg max-w-none text-muted-foreground">
          <p>
            Компания «Комплект» — надёжный партнёр в сфере подключения к инженерным сетям.
            Мы работаем с коммерческой и жилой недвижимостью по всей России.
          </p>
          <p>
            Более 10 лет опыта, сотни успешно реализованных проектов, полный цикл услуг
            от проектирования до сдачи в эксплуатацию.
          </p>
        </div>
      )}
    </div>
  )
}
