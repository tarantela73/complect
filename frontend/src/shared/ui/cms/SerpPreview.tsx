'use client'

interface SerpPreviewProps {
  title: string
  description: string
  url: string
}

export function SerpPreview({ title, description, url }: SerpPreviewProps) {
  const displayTitle = title || 'Без заголовка'
  const displayDesc = description || 'Описание отсутствует'
  const truncatedTitle = displayTitle.length > 60 ? displayTitle.slice(0, 60) + '…' : displayTitle
  const truncatedDesc = displayDesc.length > 160 ? displayDesc.slice(0, 160) + '…' : displayDesc

  return (
    <div className="rounded-lg border bg-white p-4 font-sans dark:bg-zinc-900">
      <p className="mb-1 text-xs text-muted-foreground">SERP Preview</p>
      <div className="max-w-[600px]">
        <p className="truncate text-sm text-[#202124] dark:text-zinc-100">{url}</p>
        <p
          className="mt-0.5 text-lg font-medium leading-snug text-[#1a0dab] dark:text-blue-400"
          style={{ maxWidth: 600 }}
        >
          {truncatedTitle}
        </p>
        <p className="mt-1 text-sm text-[#4d5156] dark:text-zinc-400">
          {truncatedDesc}
        </p>
      </div>
      <div className="mt-2 flex gap-4 text-xs text-muted-foreground">
        <span
          className={title && title.length > 60 ? 'text-destructive' : 'text-green-600'}
        >
          Заголовок: {title ? title.length : 0}/60
        </span>
        <span
          className={description && description.length > 160 ? 'text-destructive' : 'text-green-600'}
        >
          Описание: {description ? description.length : 0}/160
        </span>
      </div>
    </div>
  )
}
