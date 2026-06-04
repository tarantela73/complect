'use client'

import { use, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useContragent, useUpdateContragent } from '@/shared/api/contragents'
import { Button } from '@/shared/ui/button'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'

export default function EditContragentPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const router = useRouter()

  const { data, isLoading, isError } = useContragent(id)
  const updateMutation = useUpdateContragent(id)

  const [name, setName] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (data) setName(data.name)
  }, [data])

  if (isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Загрузка...</div>
  }

  if (isError || !data) {
    return (
      <div className="p-6">
        <p className="text-sm text-destructive">Контрагент не найден</p>
        <Button variant="outline" size="sm" className="mt-4" onClick={() => router.back()}>
          ← Назад
        </Button>
      </div>
    )
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (!name.trim()) { setError('Наименование обязательно'); return }

    updateMutation.mutate(
      { name: name.trim() },
      {
        onSuccess: () => router.push(`/dashboard/contragents/${id}`),
        onError: (e) => setError(e.message),
      },
    )
  }

  return (
    <div className="p-6 max-w-lg">
      <div className="mb-6 flex items-center gap-4">
        <Link
          href={`/dashboard/contragents/${id}`}
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Назад
        </Link>
        <h1 className="text-xl font-semibold">Изменить контрагента</h1>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="name">Наименование</Label>
          <Input
            id="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={255}
          />
        </div>

        <div className="space-y-1.5">
          <Label>ИНН</Label>
          <p className="text-sm text-muted-foreground font-mono">{data.inn}</p>
          <p className="text-xs text-muted-foreground">ИНН не подлежит изменению</p>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <div className="flex gap-3 pt-2">
          <Button type="submit" loading={updateMutation.isPending}>
            Сохранить
          </Button>
          <Button type="button" variant="outline" onClick={() => router.back()}>
            Отмена
          </Button>
        </div>
      </form>
    </div>
  )
}
