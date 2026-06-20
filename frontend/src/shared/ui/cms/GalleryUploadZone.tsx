'use client'

import { useRef, useState } from 'react'

interface GalleryUploadZoneProps {
  onFilesSelected: (files: File[]) => void
  isUploading: boolean
  disabled?: boolean
}

export function GalleryUploadZone({ onFilesSelected, isUploading, disabled }: GalleryUploadZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [isDragOver, setIsDragOver] = useState(false)

  function handleDragOver(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setIsDragOver(true)
  }

  function handleDragLeave(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setIsDragOver(false)
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setIsDragOver(false)
    const files = Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith('image/'))
    if (files.length > 0) onFilesSelected(files)
  }

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? [])
    if (files.length > 0) onFilesSelected(files)
    e.target.value = ''
  }

  const isDisabled = disabled || isUploading

  return (
    <div
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={[
        'flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-8 transition-colors',
        isDragOver
          ? 'border-primary bg-primary/5'
          : 'border-muted-foreground/25 hover:border-muted-foreground/50',
        isDisabled ? 'opacity-60 pointer-events-none' : 'cursor-pointer',
      ].join(' ')}
    >
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={handleInputChange}
        disabled={isDisabled}
      />

      <div className="flex flex-col items-center gap-2 text-center">
        <svg
          className="h-10 w-10 text-muted-foreground/50"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={1.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5"
          />
        </svg>

        <p className="text-sm text-muted-foreground">
          {isDragOver ? 'Отпустите для загрузки' : 'Перетащите изображения сюда или'}
        </p>

        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={isDisabled}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {isUploading ? 'Загрузка...' : 'Загрузить изображения'}
        </button>

        <p className="text-xs text-muted-foreground">PNG, JPG, GIF, WebP</p>
      </div>
    </div>
  )
}
