'use client'

import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { SortableContext, rectSortingStrategy } from '@dnd-kit/sortable'
import type { CmsImageResponse } from '@/shared/api/cms'
import { GalleryItem } from './GalleryItem'

interface GalleryGridProps {
  images: CmsImageResponse[]
  canWrite: boolean
  onReorder: (items: { id: string; order: number }[]) => void
  onDelete: (id: string) => void
  onCaptionChange: (id: string, caption: string) => void
  deletingId?: string | null
}

export function GalleryGrid({ images, canWrite, onReorder, onDelete, onCaptionChange, deletingId }: GalleryGridProps) {
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 8 },
    }),
  )

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over || active.id === over.id) return

    const oldIndex = images.findIndex((img) => img.id === active.id)
    const newIndex = images.findIndex((img) => img.id === over.id)
    if (oldIndex === -1 || newIndex === -1) return

    const reordered = [...images]
    const [moved] = reordered.splice(oldIndex, 1)
    reordered.splice(newIndex, 0, moved)

    const items = reordered.map((img, idx) => ({ id: img.id, order: idx }))
    onReorder(items)
  }

  if (images.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        Изображений пока нет
      </p>
    )
  }

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={images.map((i) => i.id)} strategy={rectSortingStrategy}>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4">
          {images.map((image) => (
            <GalleryItem
              key={image.id}
              id={image.id}
              s3Key={image.s3_key}
              caption={image.caption}
              canWrite={canWrite}
              onCaptionChange={onCaptionChange}
              onDelete={onDelete}
              isDeleting={deletingId === image.id}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  )
}
