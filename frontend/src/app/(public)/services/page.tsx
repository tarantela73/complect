import Link from 'next/link'

const SERVICES = [
  { slug: 'electricity', title: 'Электроснабжение', desc: 'Подключение к электросетям, технические условия, проектирование.' },
  { slug: 'gas', title: 'Газоснабжение', desc: 'Подключение к газовым сетям, монтаж газового оборудования.' },
  { slug: 'water', title: 'Водоснабжение', desc: 'Подключение к центральному водоснабжению и водоотведению.' },
  { slug: 'heat', title: 'Теплоснабжение', desc: 'Подключение к тепловым сетям, обслуживание ИТП.' },
  { slug: 'sewage', title: 'Водоотведение', desc: 'Проектирование и монтаж систем водоотведения.' },
  { slug: 'construction', title: 'Строительство', desc: 'Строительство инженерных объектов под ключ.' },
]

export default function ServicesPage() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-16">
      <h1 className="mb-4 text-4xl font-bold">Услуги</h1>
      <p className="mb-12 text-lg text-muted-foreground">
        Полный спектр услуг по подключению к инженерным сетям
      </p>

      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        {SERVICES.map((s) => (
          <Link
            key={s.slug}
            href={`/services/${s.slug}`}
            className="group rounded-lg border bg-card p-6 shadow-sm transition-all hover:shadow-md hover:-translate-y-0.5"
          >
            <h2 className="mb-2 text-xl font-semibold group-hover:text-primary">{s.title}</h2>
            <p className="text-sm text-muted-foreground">{s.desc}</p>
            <span className="mt-4 inline-block text-sm font-medium text-primary">
              Подробнее →
            </span>
          </Link>
        ))}
      </div>
    </div>
  )
}
