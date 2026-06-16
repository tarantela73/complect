import { LeadForm } from './components/LeadForm'

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

export default async function HomePage() {
  const content = await getCmsContent('home')

  const hero = (content.hero as Record<string, string>) ?? {}
  const title = hero.title ?? 'Надёжное энергоснабжение для вашего бизнеса'
  const subtitle =
    hero.subtitle ??
    'Поможем подключить электричество, газ, воду и тепло. Работаем с коммерческой и жилой недвижимостью.'

  return (
    <div>
      {/* Hero */}
      <section className="bg-gradient-to-b from-primary/10 to-background py-24">
        <div className="mx-auto max-w-6xl px-4 text-center">
          <h1 className="mb-6 text-4xl font-bold tracking-tight md:text-5xl">{title}</h1>
          <p className="mx-auto mb-10 max-w-2xl text-lg text-muted-foreground">{subtitle}</p>
          <a
            href="#lead-form"
            className="rounded-md bg-primary px-6 py-3 text-base font-medium text-primary-foreground hover:bg-primary/90"
          >
            Оставить заявку
          </a>
        </div>
      </section>

      {/* Services preview */}
      <section className="py-20">
        <div className="mx-auto max-w-6xl px-4">
          <h2 className="mb-12 text-center text-3xl font-bold">Наши услуги</h2>
          <div className="grid gap-6 md:grid-cols-3">
            {[
              { title: 'Электроснабжение', href: '/services/electricity' },
              { title: 'Газоснабжение', href: '/services/gas' },
              { title: 'Водоснабжение', href: '/services/water' },
              { title: 'Теплоснабжение', href: '/services/heat' },
              { title: 'Водоотведение', href: '/services/sewage' },
              { title: 'Строительство', href: '/services/construction' },
            ].map((s) => (
              <a
                key={s.href}
                href={s.href}
                className="rounded-lg border bg-card p-6 shadow-sm transition-shadow hover:shadow-md"
              >
                <h3 className="font-semibold">{s.title}</h3>
                <p className="mt-2 text-sm text-muted-foreground">
                  Профессиональное подключение и обслуживание
                </p>
              </a>
            ))}
          </div>
        </div>
      </section>

      {/* Lead form */}
      <section id="lead-form" className="bg-muted/40 py-20">
        <div className="mx-auto max-w-xl px-4">
          <h2 className="mb-8 text-center text-3xl font-bold">Оставить заявку</h2>
          <LeadForm />
        </div>
      </section>
    </div>
  )
}
