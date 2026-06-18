'use client'

interface StatusWidgetProps {
  status: string | null | undefined
  canPublish: boolean
  isPublishing: boolean
  onPublish: () => void
}

const STATUS_LABELS: Record<string, string> = {
  DRAFT: 'Черновик',
  PUBLISHED: 'Опубликовано',
  SCHEDULED: 'Запланировано',
}

const STATUS_CLASSES: Record<string, string> = {
  DRAFT: 'bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300',
  PUBLISHED: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  SCHEDULED: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
}

export function StatusWidget({ status, canPublish, isPublishing, onPublish }: StatusWidgetProps) {
  const label = status ? (STATUS_LABELS[status] ?? status) : 'Без статуса'
  const cls = status ? (STATUS_CLASSES[status] ?? STATUS_CLASSES.DRAFT) : STATUS_CLASSES.DRAFT
  const isPublished = status === 'PUBLISHED'

  return (
    <div className="flex items-center gap-3">
      <span className={`rounded-full px-3 py-1 text-xs font-medium ${cls}`}>
        {label}
      </span>
      {canPublish && !isPublished && (
        <button
          type="button"
          onClick={onPublish}
          disabled={isPublishing}
          className="rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50"
        >
          {isPublishing ? 'Публикация...' : 'Опубликовать'}
        </button>
      )}
    </div>
  )
}
