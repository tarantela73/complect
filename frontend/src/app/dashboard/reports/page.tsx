'use client'

import Link from 'next/link'

const REPORT_CARDS = [
  {
    href: '/dashboard/reports/contracts',
    title: 'Договоры',
    description: 'Список договоров с контрагентами, суммами и статусами. Фильтрация по статусу, виду ресурса, периоду и контрагенту.',
    icon: '📄',
  },
  {
    href: '/dashboard/reports/finance',
    title: 'Финансы',
    description: 'Финансовые транзакции по договорам. Фильтрация по периоду, типу операции и статусу.',
    icon: '💳',
  },
  {
    href: '/dashboard/reports/payments',
    title: 'Банковские выписки',
    description: 'Строки банковских выписок со статусом матчинга. Счётчик несопоставленных строк.',
    icon: '🏦',
  },
]

export default function ReportsIndexPage() {
  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h1 className="text-2xl font-semibold mb-2">Отчёты</h1>
      <p className="text-muted-foreground mb-8 text-sm">Экспорт данных в Excel для анализа и передачи в бухгалтерию.</p>

      <div className="grid gap-4 sm:grid-cols-3">
        {REPORT_CARDS.map((card) => (
          <Link
            key={card.href}
            href={card.href}
            className="group flex flex-col gap-3 rounded-lg border border-border bg-card p-5 hover:border-primary/50 hover:shadow-sm transition-all"
          >
            <div className="text-3xl">{card.icon}</div>
            <div>
              <h2 className="font-semibold text-base group-hover:text-primary transition-colors">{card.title}</h2>
              <p className="text-muted-foreground text-xs mt-1 leading-relaxed">{card.description}</p>
            </div>
            <span className="mt-auto text-xs font-medium text-primary">Открыть →</span>
          </Link>
        ))}
      </div>
    </div>
  )
}
