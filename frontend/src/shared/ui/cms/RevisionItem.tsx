'use client'

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

const DATE_FORMATTER = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow',
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

interface RevisionItemProps {
  revision: {
    id: string
    revision_number: number
    status: string
    label: string
    created_at: string
  }
  canWrite: boolean
  isRollingBack: boolean
  onRollback: (id: string) => void
}

export function RevisionItem({ revision, canWrite, isRollingBack, onRollback }: RevisionItemProps) {
  const statusLabel = STATUS_LABELS[revision.status] ?? revision.status
  const statusCls = STATUS_CLASSES[revision.status] ?? STATUS_CLASSES.DRAFT
  const formattedDate = DATE_FORMATTER.format(new Date(revision.created_at))

  return (
    <div className="flex items-start justify-between gap-3 border-b py-3 last:border-b-0">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-foreground">#{revision.revision_number}</span>
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusCls}`}>
            {statusLabel}
          </span>
        </div>
        {revision.label && (
          <p className="mt-0.5 truncate text-xs text-muted-foreground">{revision.label}</p>
        )}
        <p className="mt-0.5 text-xs text-muted-foreground">{formattedDate}</p>
      </div>
      {canWrite && (
        <button
          type="button"
          onClick={() => onRollback(revision.id)}
          disabled={isRollingBack}
          className="shrink-0 rounded-md border px-3 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
        >
          Откатить
        </button>
      )}
    </div>
  )
}
