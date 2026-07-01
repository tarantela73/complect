'use client'

import { use, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useAuthStore } from '@/shared/auth/store'
import {
  useTask,
  useUpdateTask,
  useChangeTaskStatus,
  useCancelTask,
  useAddComment,
  useTaskComments,
  useTaskFiles,
  useAttachFile,
  useDetachFile,
  TASK_STATUS_LABELS,
  TASK_STATUS_COLORS,
  TASK_PRIORITY_LABELS,
  TASK_PRIORITY_COLORS,
  type Task,
  type TaskComment,
  type TaskAttachment,
} from '@/shared/api/tasks'
import { useUsers } from '@/shared/api/users'
import { useFiles } from '@/shared/api/files'
import { Button } from '@/shared/ui/button'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'
import { cn } from '@/shared/ui/utils'

type Tab = 'overview' | 'comments' | 'files'

const TAB_LABELS: Record<Tab, string> = {
  overview: 'Обзор',
  comments: 'Комментарии',
  files: 'Файлы',
}

const ALLOWED_TRANSITIONS: Record<string, string[]> = {
  open: ['in_progress', 'cancelled'],
  in_progress: ['done', 'cancelled'],
  done: [],
  cancelled: [],
}

function Row({ label, value, mono = false }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex px-4 py-3 gap-4">
      <dt className="w-40 shrink-0 text-sm font-medium text-muted-foreground">{label}</dt>
      <dd className={cn('text-sm flex-1', mono && 'font-mono text-xs')}>{value}</dd>
    </div>
  )
}

function OverviewTab({ task }: { task: Task }) {
  const router = useRouter()
  const userId = useAuthStore((s) => s.userId)
  const updateTask = useUpdateTask(task.id)
  const changeStatus = useChangeTaskStatus(task.id)
  const cancelTask = useCancelTask(task.id)
  const { data: usersData } = useUsers()
  const users = usersData?.items ?? []
  const userMap = Object.fromEntries(users.map((u) => [u.user_uuid, u.full_name || u.email]))

  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(task.title)
  const [description, setDescription] = useState(task.description)
  const [priority, setPriority] = useState(task.priority)
  const [dueDate, setDueDate] = useState(task.due_date ?? '')
  const [assignedTo, setAssignedTo] = useState(task.assigned_to_id ?? '')

  const isParticipant = userId === task.created_by || userId === task.assigned_to_id
  const transitions = ALLOWED_TRANSITIONS[task.status] ?? []
  const canEdit = isParticipant && task.status !== 'done' && task.status !== 'cancelled'

  const handleSave = async () => {
    try {
      await updateTask.mutateAsync({
        title,
        description,
        priority,
        due_date: dueDate || undefined,
        assigned_to_id: assignedTo || undefined,
      })
      setEditing(false)
    } catch {}
  }

  const handleStatusChange = async (newStatus: string) => {
    try {
      await changeStatus.mutateAsync({ status: newStatus })
    } catch {}
  }

  const handleCancel = async () => {
    try {
      await cancelTask.mutateAsync()
    } catch {}
  }

  return (
    <div className="space-y-4">
      <dl className="rounded-md border border-border divide-y divide-border">
        <Row label="Статус" value={
          <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', TASK_STATUS_COLORS[task.status])}>
            {TASK_STATUS_LABELS[task.status]}
          </span>
        } />
        <Row label="Приоритет" value={
          <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', TASK_PRIORITY_COLORS[task.priority])}>
            {TASK_PRIORITY_LABELS[task.priority]}
          </span>
        } />
        <Row label="Ответственный" value={
          task.assigned_to_id
            ? (userMap[task.assigned_to_id] ?? task.assigned_to_id.slice(0, 8))
            : '—'
        } />
        <Row label="Срок" value={task.due_date ?? '—'} />
        <Row label="Создано" value={task.created_at ? new Date(task.created_at).toLocaleString('ru-RU') : '—'} />
        {task.closed_at && (
          <Row label="Закрыто" value={new Date(task.closed_at).toLocaleString('ru-RU')} />
        )}
        {task.entity_type && (
          <Row label="Связано" value={
            <Link href={`/dashboard/${task.entity_type}s/${task.entity_id}`} className="text-primary hover:underline">
              {task.entity_type} →
            </Link>
          } />
        )}
      </dl>

      {editing ? (
        <div className="rounded-md border border-border p-4 space-y-3">
          <div className="space-y-2">
            <Label>Заголовок</Label>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label>Описание</Label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm min-h-[80px]"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label>Приоритет</Label>
              <select value={priority} onChange={(e) => setPriority(e.target.value)} className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm">
                <option value="low">Низкий</option>
                <option value="normal">Нормальный</option>
                <option value="high">Высокий</option>
              </select>
            </div>
            <div className="space-y-2">
              <Label>Срок</Label>
              <Input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
            </div>
          </div>
          <div className="space-y-2">
            <Label>Ответственный</Label>
            <select value={assignedTo} onChange={(e) => setAssignedTo(e.target.value)} className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm">
              <option value="">Не назначен</option>
              {users.map((u) => (
                <option key={u.user_uuid} value={u.user_uuid}>{u.full_name || u.email}</option>
              ))}
            </select>
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={handleSave} disabled={updateTask.isPending}>Сохранить</Button>
            <Button size="sm" variant="outline" onClick={() => setEditing(false)}>Отмена</Button>
          </div>
        </div>
      ) : (
        <div>
          {task.description && (
            <div className="rounded-md border border-border p-4">
              <p className="text-sm whitespace-pre-wrap">{task.description}</p>
            </div>
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {canEdit && !editing && (
          <Button size="sm" variant="outline" onClick={() => setEditing(true)}>Редактировать</Button>
        )}
        {transitions.includes('in_progress') && (
          <Button size="sm" onClick={() => handleStatusChange('in_progress')}>Взять в работу</Button>
        )}
        {transitions.includes('done') && (
          <Button size="sm" className="bg-green-600 hover:bg-green-700" onClick={() => handleStatusChange('done')}>Выполнить</Button>
        )}
        {transitions.includes('cancelled') && (
          <Button size="sm" variant="destructive" onClick={handleCancel}>Отменить</Button>
        )}
      </div>
    </div>
  )
}

function CommentsTab({ task }: { task: Task }) {
  const userId = useAuthStore((s) => s.userId)
  const { data: comments, isLoading } = useTaskComments(task.id)
  const addComment = useAddComment(task.id)
  const [body, setBody] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!body.trim()) return
    try {
      await addComment.mutateAsync({ body: body.trim() })
      setBody('')
    } catch {}
  }

  return (
    <div className="space-y-4">
      {isLoading ? (
        <div className="py-4 text-center text-sm text-muted-foreground">Загрузка...</div>
      ) : comments && comments.length > 0 ? (
        <div className="space-y-3">
          {comments.map((c) => (
            <div key={c.id} className="rounded-md border border-border p-3">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs font-medium text-muted-foreground">
                  {c.author_id === userId ? 'Вы' : c.author_id.slice(0, 8)}
                </span>
                <span className="text-xs text-muted-foreground">
                  {c.created_at ? new Date(c.created_at).toLocaleString('ru-RU') : ''}
                </span>
              </div>
              <p className="text-sm whitespace-pre-wrap">{c.body}</p>
            </div>
          ))}
        </div>
      ) : (
        <div className="py-4 text-center text-sm text-muted-foreground">Комментариев нет</div>
      )}

      {task.status !== 'done' && task.status !== 'cancelled' && (
        <form onSubmit={handleSubmit} className="flex gap-2">
          <Input
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Написать комментарий..."
            className="flex-1"
            maxLength={10000}
          />
          <Button type="submit" disabled={!body.trim() || addComment.isPending}>
            {addComment.isPending ? '...' : 'Отправить'}
          </Button>
        </form>
      )}
    </div>
  )
}

function FilesTab({ task }: { task: Task }) {
  const { data: files, isLoading } = useTaskFiles(task.id)
  const { data: allFiles } = useFiles({ limit: 100 })
  const attachFile = useAttachFile(task.id)
  const detachFile = useDetachFile(task.id)
  const [showAttach, setShowAttach] = useState(false)

  const availableFiles = (allFiles?.results ?? []).filter(
    (f) => !files?.some((af) => af.file_id === f.id)
  )

  return (
    <div className="space-y-4">
      {isLoading ? (
        <div className="py-4 text-center text-sm text-muted-foreground">Загрузка...</div>
      ) : files && files.length > 0 ? (
        <div className="space-y-2">
          {files.map((f) => (
            <div key={f.id} className="flex items-center justify-between rounded-md border border-border px-3 py-2">
              <span className="text-sm font-mono">{f.file_id.slice(0, 8)}...</span>
              <Button
                size="sm"
                variant="ghost"
                className="text-destructive"
                onClick={() => detachFile.mutate(f.file_id)}
                disabled={detachFile.isPending}
              >
                Открепить
              </Button>
            </div>
          ))}
        </div>
      ) : (
        <div className="py-4 text-center text-sm text-muted-foreground">Файлов нет</div>
      )}

      {task.status !== 'done' && task.status !== 'cancelled' && (
        <>
          <Button size="sm" variant="outline" onClick={() => setShowAttach(!showAttach)}>
            {showAttach ? 'Закрыть' : 'Прикрепить файл'}
          </Button>

          {showAttach && availableFiles.length > 0 && (
            <div className="rounded-md border border-border p-3 space-y-2">
              {availableFiles.slice(0, 10).map((f) => (
                <div key={f.id} className="flex items-center justify-between">
                  <span className="text-sm">{f.original_name ?? f.id.slice(0, 8)}</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      attachFile.mutate({ file_id: f.id })
                      setShowAttach(false)
                    }}
                    disabled={attachFile.isPending}
                  >
                    Прикрепить
                  </Button>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default function TaskDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const router = useRouter()
  const [tab, setTab] = useState<Tab>('overview')

  const { data: task, isLoading, error } = useTask(id)

  if (isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Загрузка...</div>
  }

  if (error || !task) {
    return (
      <div className="p-6">
        <p className="text-sm text-destructive">Задача не найдена.</p>
        <Button variant="outline" className="mt-4" onClick={() => router.push('/dashboard/tasks')}>
          ← К списку
        </Button>
      </div>
    )
  }

  return (
    <div className="p-6">
      <div className="mb-4">
        <Link href="/dashboard/tasks" className="text-sm text-muted-foreground hover:underline">
          ← Задачи
        </Link>
      </div>

      <h1 className="text-xl font-semibold mb-4">{task.title}</h1>

      <div className="flex gap-1 border-b border-border mb-6">
        {(Object.keys(TAB_LABELS) as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              'px-4 py-2 text-sm font-medium transition-colors',
              tab === t
                ? 'border-b-2 border-primary text-foreground'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {TAB_LABELS[t]}
            {t === 'comments' && (
              <span className="ml-1.5 text-xs text-muted-foreground">(todo)</span>
            )}
          </button>
        ))}
      </div>

      {tab === 'overview' && <OverviewTab task={task} />}
      {tab === 'comments' && <CommentsTab task={task} />}
      {tab === 'files' && <FilesTab task={task} />}
    </div>
  )
}
