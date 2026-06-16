import Link from 'next/link'

const NAV_LINKS = [
  { href: '/', label: 'Главная' },
  { href: '/about', label: 'О компании' },
  { href: '/services', label: 'Услуги' },
  { href: '/contacts', label: 'Контакты' },
]

export function PublicHeader() {
  return (
    <header className="sticky top-0 z-50 border-b bg-white/95 backdrop-blur supports-[backdrop-filter]:bg-white/60">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4">
        <Link href="/" className="text-lg font-semibold tracking-tight text-foreground">
          Комплект
        </Link>

        <nav className="hidden items-center gap-6 md:flex">
          {NAV_LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              {link.label}
            </Link>
          ))}
        </nav>

        <Link
          href="/login"
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
        >
          Войти
        </Link>
      </div>
    </header>
  )
}
