'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useCreateContract, RESOURCE_TYPE_LABELS, RESOURCE_TYPES } from '@/shared/api/contracts'
import { Button } from '@/shared/ui/button'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'

export default function CreateContractPage() {
  const router = useRouter()
  const createMutation = useCreateContract()

  const [contractNumber, setContractNumber] = useState('')
  const [title, setTitle] = useState('')
  const [resourceType, setResourceType] = useState(RESOURCE_TYPES[0])
  const [amountRub, setAmountRub] = useState('')
  const [error, setError] = useState('')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (!contractNumber.trim()) { setError('Номер договора обязателен'); return }
    if (!title.trim()) { setError('Наименование обязательно'); return }
    if (!amountRub.trim()) { setError('Сумма договора обязательна'); return }

    const parsed = parseFloat(amountRub.replace(',', '.'))
    if (isNaN(parsed) || parsed <= 0) { setError('Сумма должна быть положительным числом'); return }

    const totalAmount = Math.round(parsed * 100)

    createMutation.mutate(
      { contract_number: contractNumber.trim(), title: title.trim(), resource_type: resourceType, total_amount: totalAmount },
      {
        onSuccess: (created) => router.push(`/dashboard/contracts/${created.id}`),
        onError: (e) => setError(e.message),
      },
    )
  }

  return (
    <div className="p-6 max-w-lg">
      <div className="mb-6 flex items-center gap-4">
        <Link href="/dashboard/contracts" className="text-sm text-muted-foreground hover:text-foreground">
          ← Договоры
        </Link>
        <h1 className="text-xl font-semibold">Новый договор</h1>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="contractNumber">Номер договора</Label>
          <Input
            id="contractNumber"
            value={contractNumber}
            onChange={(e) => setContractNumber(e.target.value)}
            placeholder="№ 123/2024"
            maxLength={100}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="title">Наименование</Label>
          <Input
            id="title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Договор поставки электроэнергии"
            maxLength={255}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="resourceType">Тип ресурса</Label>
          <select
            id="resourceType"
            value={resourceType}
            onChange={(e) => setResourceType(e.target.value)}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {RESOURCE_TYPES.map((r) => (
              <option key={r} value={r}>{RESOURCE_TYPE_LABELS[r]}</option>
            ))}
          </select>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="amount">Сумма договора (₽)</Label>
          <Input
            id="amount"
            value={amountRub}
            onChange={(e) => setAmountRub(e.target.value.replace(/[^0-9.,]/g, ''))}
            placeholder="1 500 000.00"
          />
          <p className="text-xs text-muted-foreground">Введите сумму в рублях</p>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <div className="flex gap-3 pt-2">
          <Button type="submit" loading={createMutation.isPending}>
            Создать
          </Button>
          <Button type="button" variant="outline" onClick={() => router.back()}>
            Отмена
          </Button>
        </div>
      </form>
    </div>
  )
}
