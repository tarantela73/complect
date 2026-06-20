'use client'

import { useEffect, useRef, useState } from 'react'
import { useUpdateObject } from '@/shared/api/objects'
import { useUploadFile, useFile } from '@/shared/api/files'
import { Button } from '@/shared/ui/button'
import { cn } from '@/shared/ui/utils'

interface PublicSiteSectionProps {
  objectId: string
  isPublic: boolean
  slug: string | null | undefined
  coverImageKey: string | null | undefined
  canEdit: boolean
}

export function PublicSiteSection({
  objectId,
  isPublic,
  slug,
  coverImageKey,
  canEdit,
}: PublicSiteSectionProps) {
  const updateObject = useUpdateObject(objectId)

  // --- is_public toggle ---
  const [localPublic, setLocalPublic] = useState(isPublic)
  useEffect(() => { setLocalPublic(isPublic) }, [isPublic])

  function handleTogglePublic() {
    const next = !localPublic
    setLocalPublic(next)
    updateObject.mutate({ is_public: next })
  }

  // --- slug editor ---
  const [editingSlug, setEditingSlug] = useState(false)
  const [slugInput, setSlugInput] = useState(slug ?? '')
  const [slugError, setSlugError] = useState('')
  useEffect(() => { setSlugInput(slug ?? '') }, [slug])

  function handleSaveSlug() {
    if (!slugInput.trim()) {
      setSlugError('Slug не может быть пустым')
      return
    }
    setSlugError('')
    updateObject.mutate(
      { slug: slugInput.trim() },
      {
        onSuccess: () => setEditingSlug(false),
        onError: (e) => setSlugError((e as Error).message),
      },
    )
  }

  // --- cover image upload ---
  const fileInputRef = useRef<HTMLInputElement>(null)
  const uploadFile = useUploadFile()
  const [pendingFileId, setPendingFileId] = useState<string | null>(null)
  const [uploadState, setUploadState] = useState<'idle' | 'uploading' | 'scanning' | 'done' | 'error'>('idle')

  const { data: pendingFile } = useFile(pendingFileId ?? '')

  useEffect(() => {
    if (!pendingFile || !pendingFileId) return
    if (pendingFile.status === 'AVAILABLE') {
      updateObject.mutate(
        { cover_image_key: pendingFile.object_key },
        {
          onSuccess: () => {
            setUploadState('done')
            setPendingFileId(null)
          },
          onError: () => setUploadState('error'),
        },
      )
    } else if (pendingFile.status === 'QUARANTINED') {
      setUploadState('error')
      setPendingFileId(null)
    }
  }, [pendingFile?.status])  // eslint-disable-line react-hooks/exhaustive-deps

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploadState('uploading')
    uploadFile.mutate(file, {
      onSuccess: (result) => {
        setUploadState('scanning')
        setPendingFileId(result.id)
      },
      onError: () => setUploadState('error'),
    })
    // reset input so same file can be re-selected if needed
    e.target.value = ''
  }

  return (
    <div className="mt-6 rounded-md border border-border">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-sm font-medium">Публикация на сайте</h3>
      </div>

      {/* is_public toggle */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div>
          <p className="text-sm font-medium">Опубликовать на сайте</p>
          <p className="text-xs text-muted-foreground">
            Объект будет виден в разделе «Наши объекты»
          </p>
        </div>
        <button
          onClick={canEdit ? handleTogglePublic : undefined}
          disabled={!canEdit || updateObject.isPending}
          className={cn(
            'relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            localPublic ? 'bg-primary' : 'bg-input',
            (!canEdit || updateObject.isPending) && 'opacity-50 cursor-not-allowed',
          )}
          role="switch"
          aria-checked={localPublic}
        >
          <span
            className={cn(
              'inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform',
              localPublic ? 'translate-x-6' : 'translate-x-1',
            )}
          />
        </button>
      </div>

      {/* slug */}
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <p className="text-xs text-muted-foreground mb-1">URL-адрес (slug)</p>
            {editingSlug ? (
              <div className="flex items-center gap-2">
                <input
                  className="flex-1 min-w-0 rounded-md border border-border bg-background px-3 py-1.5 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-ring"
                  value={slugInput}
                  onChange={(e) => setSlugInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSaveSlug()}
                  placeholder="moy-obekt"
                  autoFocus
                />
                <Button size="sm" onClick={handleSaveSlug} disabled={updateObject.isPending}>
                  Сохранить
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => { setEditingSlug(false); setSlugError(''); setSlugInput(slug ?? '') }}
                >
                  Отмена
                </Button>
              </div>
            ) : (
              <p className="text-sm font-mono">{slug || <span className="text-muted-foreground italic">не задан</span>}</p>
            )}
            {slugError && <p className="mt-1 text-xs text-destructive">{slugError}</p>}
          </div>
          {canEdit && !editingSlug && (
            <Button size="sm" variant="outline" className="shrink-0 mt-4" onClick={() => setEditingSlug(true)}>
              Изменить
            </Button>
          )}
        </div>
      </div>

      {/* cover image */}
      <div className="px-4 py-3">
        <p className="text-xs text-muted-foreground mb-2">Обложка</p>
        {coverImageKey ? (
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted-foreground font-mono truncate max-w-xs">
              {coverImageKey}
            </span>
            <span className="text-xs text-green-600">✓ загружена</span>
            {canEdit && (
              <Button size="sm" variant="outline" onClick={() => fileInputRef.current?.click()} disabled={uploadState === 'uploading' || uploadState === 'scanning'}>
                Изменить
              </Button>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">Обложка не загружена</span>
            {canEdit && (
              <Button size="sm" variant="outline" onClick={() => fileInputRef.current?.click()} disabled={uploadState === 'uploading' || uploadState === 'scanning'}>
                Загрузить
              </Button>
            )}
          </div>
        )}

        {uploadState === 'uploading' && (
          <p className="mt-2 text-xs text-muted-foreground">Загрузка файла...</p>
        )}
        {uploadState === 'scanning' && (
          <p className="mt-2 text-xs text-muted-foreground">Проверка антивирусом...</p>
        )}
        {uploadState === 'done' && (
          <p className="mt-2 text-xs text-green-600">Обложка успешно сохранена</p>
        )}
        {uploadState === 'error' && (
          <p className="mt-2 text-xs text-destructive">Ошибка загрузки. Попробуйте снова.</p>
        )}

        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={handleFileChange}
        />
      </div>
    </div>
  )
}
