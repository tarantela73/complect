import Link from 'next/link'

export default function NotFound() {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="text-center">
        <p className="text-6xl font-semibold text-muted-foreground">404</p>
        <p className="mt-2 text-sm text-muted-foreground">Страница не найдена</p>
        <Link href="/" className="mt-4 inline-block text-sm underline">
          На главную
        </Link>
      </div>
    </div>
  )
}
