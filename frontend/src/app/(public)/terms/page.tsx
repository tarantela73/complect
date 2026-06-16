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

export default async function TermsPage() {
  const content = await getCmsContent('terms')

  return (
    <div className="mx-auto max-w-4xl px-4 py-16">
      <h1 className="mb-8 text-4xl font-bold">Условия использования</h1>
      {content.body ? (
        <TiptapRenderer
          content={content.body as Record<string, unknown>}
          className="prose prose-lg max-w-none"
        />
      ) : (
        <p className="text-muted-foreground">Документ находится в разработке.</p>
      )}
    </div>
  )
}
