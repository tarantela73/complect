'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useTasks, useCreateTask, TASK_STATUS_LABELS, TASK_STATUS_COLORS, TASK_PRIORITY_LABELS, TASK_PRIORITY_COLORS } from '@/shared/api/tasks'
import { Button } from '@/shared/ui/button'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'
import { cn } from '@/shared/ui/utils'

const STATUS_OPTIONS = [
  { value: '', label: 'Все статусы' },
  { value: 'open', label: 'Открыта' },
  { value: 'in_progress', label: 'В работе' },
  { value: 'done', label: 'Выполнена' },
  { value: 'cancelled', label: 'Отменена' },
]

export default function TasksPage() {
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [showCreate, setShowCreate] = useState(false)
  const [cursor, setCursor] = useState<string | undefined>()

  const { data, isLoading } = useTasks({
    status: statusFilter || undefined,
    limit: 20,
    cursor,
  })

  const tasks = data?.items ?? []
  const nextCursor = data?.next_cursor

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold">Задачи</h1>
        <Button onClick={() => setShowCreate(!showCreate)}>
          {showCreate ? 'Закрыть' : 'Новая задача'}
        </Button>
      </div>

      {showCreate && <CreateTaskForm onCreated={() => setShowCreate(false)} />}

      <div className="mb-4">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-md border border-border bg-background px-3 py-2 text-sm"
        >
          {STATUS_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>

      {isLoading ? (
        <div className="py-8 text-center text-sm text-muted-foreground">Загрузка...</div>
      ) : tasks.length === 0 ? (
        <div className="py-8 text-center text-sm text-muted-foreground">Задач нет</div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/50">
              <tr>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Заголовок</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Приоритет</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Статус</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Срок</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Создано</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => (
                <tr key={task.id} className="border-t border-border hover:bg-muted/30">
                  <td className="px-4 py-3">
                    <Link href={`/dashboard/tasks/${task.id}`} className="text-sm font-medium hover:underline">
                      {task.title}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', TASK_PRIORITY_COLORS[task.priority])}>
                      {TASK_PRIORITY_LABELS[task.priority] ?? task.priority}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', TASK_STATUS_COLORS[task.status])}>
                      {TASK_STATUS_LABELS[task.status] ?? task.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm text-muted-foreground">
                    {task.due_date ?? '—'}
                  </td>
                  <td className="px-4 py-3 text-sm text-muted-foreground">
                    {task.created_at ? new Date(task.created_at).toLocaleDateString('ru-RU') : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {nextCursor && (
        <div className="mt-4 text-center">
          <Button variant="outline" onClick={() => setCursor(nextCursor)}>Загрузить ещё</Button>
        </div>
      )}
    </div>
  )
}

function CreateTaskForm({ onCreated }: { onCreated: () => void }) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState('normal')
  const [dueDate, setDueDate] = useState('')
  const [error, setError] = useState<string | null>(null)

  const createTask = useCreateTask()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      await createTask.mutateAsync({
        title,
        description,
        priority,
        due_date: dueDate || undefined,
        entity_type: '',
      })
      onCreated()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="mb-6 rounded-lg border bg-card p-4 space-y-4">
      <h2 className="text-sm font-semibold">Новая задача</h2>

      <div className="space-y-2">
        <Label htmlFor="title">Заголовок</Label>
        <Input id="title" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={255} />
      </div>

      <div className="space-y-2">
        <Label htmlFor="description">Описание</Label>
        <textarea
          id="description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm min-h-[80px]"
          maxLength={10000}
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="priority">Приоритет</Label>
          <select
            id="priority"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          >
            <option value="low">Низкий</option>
            <option value="normal">Нормальный</option>
            <option value="high">Высокий</option>
          </select>
        </div>

        <div className="space-y-2">
          <Label htmlFor="due_date">Срок</Label>
          <Input id="due_date" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
        </div>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="flex gap-2">
        <Button type="submit" disabled={createTask.isPending}>
          {createTask.isPending ? 'Создание...' : 'Создать'}
        </Button>
        <Button type="button" variant="outline" onClick={onCreated}>Отмена</Button>
      </div>
    </form>
  )
}
