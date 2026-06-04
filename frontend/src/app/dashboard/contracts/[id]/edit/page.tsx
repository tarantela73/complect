'use client'

import { use, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useContract, useUpdateContract } from '@/shared/api/contracts'
import { Button } from '@/shared/ui/button'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'

export default function EditContractPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const router = useRouter()

  const { data, isLoading, isError } = useContract(id)
  const updateMutation = useUpdateContract(id)

  const [title, setTitle] = useState('')
  const [amountRub, setAmountRub] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (data) {
      setTitle(data.title)
      setAmountRub((data.total_amount / 100).toFixed(2))
    }
  }, [data])

  if (isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Загрузка...</div>
  }

  if (isError || !data) {
    return (
      <div className="p-6">
        <p className="text-sm text-destructive">Договор не найден</p>
        <Button variant="outline" size="sm" className="mt-4" onClick={() => router.back()}>
          ← Назад
        </Button>
      </div>
    )
  }

  if (data.status !== 'DRAFT') {
    return (
      <div className="p-6">
        <p className="text-sm text-destructive">
          Редактирование доступно только для договоров в статусе DRAFT.
        </p>
        <Button
          variant="outline"
          size="sm"
          className="mt-4"
          onClick={() => router.push(`/dashboard/contracts/${id}`)}
        >
          ← К договору
        </Button>
      </div>
    )
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (!title.trim()) { setError('Наименование обязательно'); return }

    const parsed = parseFloat(amountRub.replace(',', '.'))
    if (isNaN(parsed) || parsed <= 0) { setError('Сумма должна быть положительным числом'); return }

    const totalAmount = Math.round(parsed * 100)
    const updates: { title?: string; total_amount?: number } = {}

    if (title.trim() !== data.title) updates.title = title.trim()
    if (totalAmount !== data.total_amount) updates.total_amount = totalAmount

    if (Object.keys(updates).length === 0) {
      router.push(`/dashboard/contracts/${id}`)
      return
    }

    updateMutation.mutate(updates, {
      onSuccess: () => router.push(`/dashboard/contracts/${id}`),
      onError: (e) => setError(e.message),
    })
  }

  return (
    <div className="p-6 max-w-lg">
      <div className="mb-6 flex items-center gap-4">
        <Link
          href={`/dashboard/contracts/${id}`}
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Назад
        </Link>
        <h1 className="text-xl font-semibold">Изменить договор</h1>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label>Номер договора</Label>
          <p className="text-sm text-muted-foreground font-mono">{data.contract_number}</p>
          <p className="text-xs text-muted-foreground">Номер договора не подлежит изменению</p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="title">Наименование</Label>
          <Input
            id="title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={255}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="amount">Сумма договора (₽)</Label>
          <Input
            id="amount"
            value={amountRub}
            onChange={(e) => setAmountRub(e.target.value.replace(/[^0-9.,]/g, ''))}
          />
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
