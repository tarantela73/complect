'use client'

import { use, useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useEditor, EditorContent } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { useCan } from '@/shared/auth/hooks'
import { useCmsPage, useSaveCmsPage, usePublishCmsPage } from '@/shared/api/cms'
import type { TiptapDocSchema } from '@/shared/api/cms'
import { GalleryManager, MetaEditor, RevisionHistoryDrawer, SerpPreview, StatusWidget } from '@/shared/ui/cms'
import type { MetaFields } from '@/shared/ui/cms'

export default function CmsSectionPage({ params }: { params: Promise<{ section: string }> }) {
  const { section } = use(params)
  const router = useRouter()
  const canWrite = useCan('cms.write')
  const canRead = useCan('cms.read')
  const canPublish = useCan('cms.publish')

  const { data: page, isLoading } = useCmsPage(section)
  const { mutate: savePage, isPending: isSaving, isSuccess: isSaved } = useSaveCmsPage(section)
  const { mutate: publishPage, isPending: isPublishing } = usePublishCmsPage(section)

  const [metaFields, setMetaFields] = useState<MetaFields>({})
  const [isHistoryOpen, setIsHistoryOpen] = useState(false)

  useEffect(() => {
    if (page?.meta) {
      const { _overrides: _o, ...rest } = page.meta as Record<string, unknown>
      setMetaFields(rest as MetaFields)
    }
  }, [page?.meta])

  const editor = useEditor({
    extensions: [StarterKit],
    editable: canWrite,
    content: page?.content ?? { type: 'doc', content: [] },
  })

  useEffect(() => {
    if (page?.content && editor && !editor.isDestroyed) {
      editor.commands.setContent(page.content as Parameters<typeof editor.commands.setContent>[0])
    }
  }, [page?.content, editor])

  useEffect(() => {
    if (!canRead && !isLoading) {
      router.replace('/dashboard')
    }
  }, [canRead, isLoading, router])

  const handleSaveContent = useCallback(() => {
    if (!editor) return
    savePage({ content: editor.getJSON() as unknown as TiptapDocSchema })
  }, [editor, savePage])

  const handleSaveMeta = useCallback(() => {
    const cleanedMeta: Record<string, string | undefined> = {}
    for (const [k, v] of Object.entries(metaFields)) {
      if (v !== undefined && v !== '') cleanedMeta[k] = v
    }
    savePage({ meta: Object.keys(cleanedMeta).length ? cleanedMeta : undefined })
  }, [metaFields, savePage])

  const handlePublish = useCallback(() => {
    publishPage()
  }, [publishPage])

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center text-muted-foreground">
        Загрузка...
      </div>
    )
  }

  const publicPath = section.startsWith('services-')
    ? `/services/${section.replace('services-', '')}`
    : `/${section === 'home' ? '' : section}`

  const overrides = ((page?.meta as Record<string, unknown>)?._overrides ?? {}) as Record<string, boolean>
  const baseUrl = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://example.com'
  const pageUrl = `${baseUrl}${publicPath}`

  return (
    <div className="p-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <button
            onClick={() => router.back()}
            className="mb-2 text-sm text-muted-foreground hover:text-foreground"
          >
            ← Назад к разделам
          </button>
          <h1 className="text-2xl font-semibold">{section}</h1>
        </div>
        <div className="flex items-center gap-3">
          <StatusWidget
            status={page?.status}
            canPublish={canPublish}
            isPublishing={isPublishing}
            onPublish={handlePublish}
            onHistory={() => setIsHistoryOpen(true)}
          />
          <a
            href={publicPath}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-md border px-4 py-2 text-sm hover:bg-muted"
          >
            Предпросмотр
          </a>
          {canWrite && (
            <button
              onClick={handleSaveContent}
              disabled={isSaving}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {isSaving ? 'Сохранение...' : 'Сохранить'}
            </button>
          )}
        </div>
      </div>

      {isSaved && (
        <div className="mb-4 rounded-md bg-green-50 px-4 py-3 text-sm text-green-800 dark:bg-green-900/20 dark:text-green-400">
          Изменения сохранены
        </div>
      )}

      <div className="space-y-4">
        <div className="rounded-lg border bg-card shadow-sm">
          <div className="border-b p-3">
            <p className="text-xs text-muted-foreground">
              {canWrite ? 'Редактор контента' : 'Просмотр контента (нет прав на редактирование)'}
            </p>
          </div>
          <div className="p-4">
            <EditorContent
              editor={editor}
              className="prose prose-sm max-w-none min-h-75 focus-within:outline-none [&_.ProseMirror]:outline-none [&_.ProseMirror]:min-h-70"
            />
          </div>
        </div>

        {canWrite && (
          <MetaEditor
            meta={metaFields}
            overrides={overrides}
            onChange={setMetaFields}
            onSave={handleSaveMeta}
            isSaving={isSaving}
          />
        )}

        <SerpPreview
          title={metaFields.title ?? ''}
          description={metaFields.description ?? ''}
          url={pageUrl}
        />

        <GalleryManager section={section} canWrite={canWrite} />
      </div>

      <RevisionHistoryDrawer
        slug={section}
        isOpen={isHistoryOpen}
        onClose={() => setIsHistoryOpen(false)}
        canWrite={canWrite}
        onRollbackSuccess={() => setIsHistoryOpen(false)}
      />
    </div>
  )
}
