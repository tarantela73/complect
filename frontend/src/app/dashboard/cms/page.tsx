'use client'

import Link from 'next/link'
import { useCan } from '@/shared/auth/hooks'

const CMS_SECTIONS = [
  { slug: 'home', title: 'Главная', desc: 'Hero, услуги, форма заявки' },
  { slug: 'about', title: 'О компании', desc: 'История, команда, преимущества' },
  { slug: 'services-electricity', title: 'Услуга: Электроснабжение', desc: '/services/electricity' },
  { slug: 'services-gas', title: 'Услуга: Газоснабжение', desc: '/services/gas' },
  { slug: 'services-water', title: 'Услуга: Водоснабжение', desc: '/services/water' },
  { slug: 'services-heat', title: 'Услуга: Теплоснабжение', desc: '/services/heat' },
  { slug: 'services-sewage', title: 'Услуга: Водоотведение', desc: '/services/sewage' },
  { slug: 'services-construction', title: 'Услуга: Строительство', desc: '/services/construction' },
  { slug: 'contacts', title: 'Контакты', desc: 'Адрес, телефон, email' },
  { slug: 'privacy', title: 'Конфиденциальность', desc: 'Политика конфиденциальности' },
  { slug: 'terms', title: 'Условия', desc: 'Условия использования' },
]

export default function CmsDashboardPage() {
  const canWrite = useCan('cms.write')

  return (
    <div className="p-8">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Управление контентом сайта</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Редактирование текстов и изображений публичного сайта
          </p>
        </div>
        <a
          href="/"
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm text-primary hover:underline"
        >
          Открыть сайт →
        </a>
      </div>

      {!canWrite && (
        <div className="mb-6 rounded-md bg-yellow-50 px-4 py-3 text-sm text-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-400">
          У вас нет прав на редактирование. Обратитесь к администратору для получения роли{' '}
          <code>cms_editor</code>.
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {CMS_SECTIONS.map((section) => (
          <Link
            key={section.slug}
            href={`/dashboard/cms/${section.slug}`}
            className="group rounded-lg border bg-card p-5 shadow-sm transition-all hover:shadow-md hover:-translate-y-0.5"
          >
            <h2 className="font-semibold group-hover:text-primary">{section.title}</h2>
            <p className="mt-1 text-sm text-muted-foreground">{section.desc}</p>
          </Link>
        ))}
      </div>
    </div>
  )
}
