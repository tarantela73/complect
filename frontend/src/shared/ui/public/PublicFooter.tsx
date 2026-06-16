import Link from 'next/link'

export function PublicFooter() {
  return (
    <footer className="border-t bg-muted/40 py-8">
      <div className="mx-auto max-w-6xl px-4">
        <div className="flex flex-col items-center justify-between gap-4 md:flex-row">
          <p className="text-sm text-muted-foreground">
            © {new Date().getFullYear()} Комплект. Все права защищены.
          </p>
          <div className="flex gap-4">
            <Link href="/privacy" className="text-sm text-muted-foreground hover:text-foreground">
              Политика конфиденциальности
            </Link>
            <Link href="/terms" className="text-sm text-muted-foreground hover:text-foreground">
              Условия использования
            </Link>
          </div>
        </div>
      </div>
    </footer>
  )
}
