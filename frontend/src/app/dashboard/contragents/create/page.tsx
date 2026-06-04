'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { useCreateContragent, CONTRAGENT_TYPES, CONTRAGENT_TYPE_LABELS } from '@/shared/api/contragents'
import { Button } from '@/shared/ui/button'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'

export default function CreateContragentPage() {
  const router = useRouter()
  const createMutation = useCreateContragent()

  const [name, setName] = useState('')
  const [inn, setInn] = useState('')
  const [contragentType, setContragentType] = useState('LEGAL_ENTITY')
  const [error, setError] = useState('')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (!name.trim()) { setError('Наименование обязательно'); return }
    if (inn.length < 10) { setError('ИНН должен содержать минимум 10 цифр'); return }

    createMutation.mutate(
      { name: name.trim(), inn: inn.trim(), contragent_type: contragentType },
      {
        onSuccess: (created) => router.push(`/dashboard/contragents/${created.id}`),
        onError: (e) => setError(e.message),
      },
    )
  }

  return (
    <div className="p-6 max-w-lg">
      <div className="mb-6 flex items-center gap-4">
        <button
          onClick={() => router.back()}
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Назад
        </button>
        <h1 className="text-xl font-semibold">Новый контрагент</h1>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="name">Наименование</Label>
          <Input
            id="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="ООО «Ромашка»"
            maxLength={255}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="inn">ИНН</Label>
          <Input
            id="inn"
            value={inn}
            onChange={(e) => setInn(e.target.value.replace(/\D/g, ''))}
            placeholder="1234567890"
            maxLength={12}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="type">Тип контрагента</Label>
          <select
            id="type"
            value={contragentType}
            onChange={(e) => setContragentType(e.target.value)}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            {CONTRAGENT_TYPES.map((t) => (
              <option key={t} value={t}>{CONTRAGENT_TYPE_LABELS[t]}</option>
            ))}
          </select>
        </div>

        {error && (
          <p className="text-sm text-destructive">{error}</p>
        )}

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
