'use client'

import { useState } from 'react'
import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'

interface GalleryItemProps {
  id: string
  s3Key: string
  caption: string
  canWrite: boolean
  onCaptionChange: (id: string, caption: string) => void
  onDelete: (id: string) => void
  isDeleting?: boolean
}

export function GalleryItem({ id, s3Key, caption, canWrite, onCaptionChange, onDelete, isDeleting }: GalleryItemProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id })
  const style = { transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.4 : 1 }

  const [localCaption, setLocalCaption] = useState(caption)

  const isStub = s3Key.startsWith('stub/')
  const imgSrc = isStub ? null : `/api/v1/cms/gallery/image/${s3Key}`

  return (
    <div
      ref={setNodeRef}
      style={style}
      className="group relative rounded-lg border bg-card shadow-sm overflow-hidden"
    >
      {/* Drag handle */}
      {canWrite && (
        <div
          {...attributes}
          {...listeners}
          className="absolute left-2 top-2 z-10 rounded bg-black/40 p-1 opacity-0 group-hover:opacity-100 transition-opacity cursor-grab active:cursor-grabbing"
          title="Перетащить"
        >
          <svg className="h-4 w-4 text-white" fill="currentColor" viewBox="0 0 24 24">
            <path d="M8 6a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM8 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM8 22a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM16 6a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM16 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM16 22a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z" />
          </svg>
        </div>
      )}

      {/* Delete button */}
      {canWrite && (
        <button
          type="button"
          onClick={() => onDelete(id)}
          disabled={isDeleting}
          className="absolute right-2 top-2 z-10 rounded bg-black/40 p-1 opacity-0 group-hover:opacity-100 transition-opacity hover:bg-red-600/80 disabled:opacity-50"
          title="Удалить"
        >
          <svg className="h-4 w-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
          </svg>
        </button>
      )}

      {/* Thumbnail */}
      <div className="aspect-square bg-muted flex items-center justify-center overflow-hidden">
        {imgSrc ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={imgSrc}
            alt={localCaption || s3Key}
            className="h-full w-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="flex flex-col items-center gap-1 text-muted-foreground p-4">
            <svg className="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="m2.25 15.75 5.159-5.159a2.25 2.25 0 0 1 3.182 0l5.159 5.159m-1.5-1.5 1.409-1.409a2.25 2.25 0 0 1 3.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 0 0 1.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H3.75A1.5 1.5 0 0 0 2.25 6v12a1.5 1.5 0 0 0 1.5 1.5Zm10.5-11.25h.008v.008h-.008V8.25Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z" />
            </svg>
            <span className="text-xs truncate max-w-full">{s3Key}</span>
          </div>
        )}
      </div>

      {/* Caption */}
      <div className="p-2">
        <input
          type="text"
          value={localCaption}
          onChange={(e) => setLocalCaption(e.target.value)}
          onBlur={() => {
            if (localCaption !== caption) {
              onCaptionChange(id, localCaption)
            }
          }}
          readOnly={!canWrite}
          placeholder="Подпись..."
          className="w-full rounded border-0 bg-transparent px-1 py-0.5 text-xs text-muted-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-primary read-only:cursor-default"
        />
      </div>
    </div>
  )
}
