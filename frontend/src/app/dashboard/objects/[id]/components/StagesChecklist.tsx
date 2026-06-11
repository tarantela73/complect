'use client'

import {
  STAGE_KEY_LABELS,
  STAGE_STATUS_LABELS,
  useUpdateObjectStage,
  type ObjectStage,
} from '@/shared/api/objects'
import { Button } from '@/shared/ui/button'

const STAGE_ICON: Record<string, string> = {
  PENDING: '⬜',
  IN_PROGRESS: '🔵',
  COMPLETED: '✅',
  SKIPPED: '➖',
}

function StageRow({
  stage,
  objectId,
}: {
  stage: ObjectStage
  objectId: string
}) {
  const update = useUpdateObjectStage(objectId)
  const isPending = update.isPending

  const act = (status: string) =>
    update.mutate({ stageKey: stage.stage_key, body: { status } })

  return (
    <li className="flex items-start gap-3 py-3 border-b border-border last:border-0">
      <span className="mt-0.5 text-lg leading-none select-none" title={STAGE_STATUS_LABELS[stage.status]}>
        {STAGE_ICON[stage.status] ?? '⬜'}
      </span>

      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium">
          {STAGE_KEY_LABELS[stage.stage_key] ?? stage.stage_key}
          {stage.is_optional && (
            <span className="ml-2 text-xs text-muted-foreground font-normal">(необязательный)</span>
          )}
        </p>
        {stage.completed_at && (
          <p className="text-xs text-muted-foreground mt-0.5">
            {new Date(stage.completed_at).toLocaleString('ru-RU')}
          </p>
        )}
        {stage.note && (
          <p className="text-xs text-muted-foreground mt-0.5 italic">{stage.note}</p>
        )}
      </div>

      <div className="flex gap-1.5 shrink-0 flex-wrap justify-end">
        {stage.status === 'PENDING' && (
          <>
            <Button size="sm" variant="outline" disabled={isPending} onClick={() => act('IN_PROGRESS')}>
              В работе
            </Button>
            {stage.is_optional && (
              <Button size="sm" variant="ghost" disabled={isPending} onClick={() => act('SKIPPED')}>
                Пропустить
              </Button>
            )}
          </>
        )}

        {stage.status === 'IN_PROGRESS' && (
          <>
            <Button size="sm" disabled={isPending} onClick={() => act('COMPLETED')}>
              Завершить
            </Button>
            <Button size="sm" variant="ghost" disabled={isPending} onClick={() => act('PENDING')}>
              Вернуть
            </Button>
          </>
        )}

        {(stage.status === 'COMPLETED' || stage.status === 'SKIPPED') && (
          <Button size="sm" variant="ghost" disabled={isPending} onClick={() => act('PENDING')}>
            Вернуть
          </Button>
        )}
      </div>
    </li>
  )
}

export function StagesChecklist({
  stages,
  objectId,
}: {
  stages: ObjectStage[]
  objectId: string
}) {
  if (stages.length === 0) {
    return <p className="text-sm text-muted-foreground py-4">Этапы не найдены</p>
  }

  const completed = stages.filter((s) => s.status === 'COMPLETED' || s.status === 'SKIPPED').length

  return (
    <div className="mt-2">
      <p className="text-xs text-muted-foreground mb-3">
        Завершено {completed} из {stages.length}
      </p>
      <ul className="rounded-md border border-border divide-y divide-border overflow-hidden px-4">
        {stages.map((s) => (
          <StageRow key={s.stage_key} stage={s} objectId={objectId} />
        ))}
      </ul>
    </div>
  )
}
