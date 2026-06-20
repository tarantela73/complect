'use client'

import { useState } from 'react'
import {
  useCmsGallery,
  useDeleteGalleryImage,
  useUpdateGalleryImage,
  useReorderGallery,
  useCmsGalleryUpload,
} from '@/shared/api/cms'
import { GalleryGrid } from './GalleryGrid'
import { GalleryUploadZone } from './GalleryUploadZone'

interface GalleryManagerProps {
  section: string
  canWrite: boolean
}

interface UploadProgress {
  filename: string
  status: 'pending' | 'uploading' | 'done' | 'error'
  error?: string
}

export function GalleryManager({ section, canWrite }: GalleryManagerProps) {
  const { data: galleryData, isLoading } = useCmsGallery(section)
  const { mutateAsync: deleteImage, isPending: isDeleting, variables: deletingId } = useDeleteGalleryImage(section)
  const { mutateAsync: updateImage } = useUpdateGalleryImage(section)
  const { mutateAsync: reorderImages } = useReorderGallery()
  const { mutateAsync: uploadImage } = useCmsGalleryUpload(section)

  const [uploadProgress, setUploadProgress] = useState<UploadProgress[]>([])

  const images = galleryData?.results ?? []
  const isUploading = uploadProgress.some((p) => p.status === 'uploading' || p.status === 'pending')

  async function handleFilesSelected(files: File[]) {
    const progress: UploadProgress[] = files.map((f) => ({ filename: f.name, status: 'pending' }))
    setUploadProgress(progress)

    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      setUploadProgress((prev) =>
        prev.map((p, idx) => (idx === i ? { ...p, status: 'uploading' } : p)),
      )

      try {
        await uploadImage({
          file,
          caption: '',
          order: images.length + i,
        })
        setUploadProgress((prev) =>
          prev.map((p, idx) => (idx === i ? { ...p, status: 'done' } : p)),
        )
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Ошибка загрузки'
        setUploadProgress((prev) =>
          prev.map((p, idx) => (idx === i ? { ...p, status: 'error', error: message } : p)),
        )
      }
    }

    // Clear progress after 3 seconds when all done
    setTimeout(() => setUploadProgress([]), 3000)
  }

  async function handleReorder(items: { id: string; order: number }[]) {
    await reorderImages(items)
  }

  async function handleDelete(id: string) {
    await deleteImage(id)
  }

  async function handleCaptionChange(id: string, caption: string) {
    await updateImage({ imageId: id, body: { caption } })
  }

  if (isLoading) {
    return (
      <div className="rounded-lg border bg-card shadow-sm">
        <div className="p-6 text-center text-sm text-muted-foreground">Загрузка галереи...</div>
      </div>
    )
  }

  return (
    <div className="rounded-lg border bg-card shadow-sm">
      <div className="border-b p-4">
        <h2 className="text-base font-semibold">
          Галерея
          <span className="ml-2 text-sm font-normal text-muted-foreground">
            {images.length} {images.length === 1 ? 'изображение' : images.length >= 2 && images.length <= 4 ? 'изображения' : 'изображений'}
          </span>
        </h2>
      </div>

      <div className="space-y-4 p-4">
        {canWrite && (
          <GalleryUploadZone
            onFilesSelected={handleFilesSelected}
            isUploading={isUploading}
            disabled={!canWrite}
          />
        )}

        {/* Upload progress */}
        {uploadProgress.length > 0 && (
          <div className="space-y-1">
            {uploadProgress.map((p, idx) => (
              <div
                key={idx}
                className={[
                  'flex items-center justify-between rounded px-3 py-1.5 text-xs',
                  p.status === 'error'
                    ? 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400'
                    : p.status === 'done'
                    ? 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400'
                    : 'bg-muted text-muted-foreground',
                ].join(' ')}
              >
                <span className="truncate">{p.filename}</span>
                <span className="ml-2 shrink-0">
                  {p.status === 'pending' && 'Ожидание...'}
                  {p.status === 'uploading' && 'Загрузка...'}
                  {p.status === 'done' && 'Готово'}
                  {p.status === 'error' && (p.error ?? 'Ошибка')}
                </span>
              </div>
            ))}
          </div>
        )}

        <GalleryGrid
          images={images}
          canWrite={canWrite}
          onReorder={handleReorder}
          onDelete={handleDelete}
          onCaptionChange={handleCaptionChange}
          deletingId={isDeleting ? (deletingId as string) : null}
        />
      </div>
    </div>
  )
}
