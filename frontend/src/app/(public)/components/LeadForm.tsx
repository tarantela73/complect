'use client'

import { useRef, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Turnstile, type TurnstileInstance } from '@marsidev/react-turnstile'
import { Label } from '@/shared/ui/label'
import { Input } from '@/shared/ui/input'
import { Button } from '@/shared/ui/button'
import { cn } from '@/shared/ui/utils'
import { useSubmitLeadForm } from '@/shared/api/public'
import { env } from '@/shared/config/env'

const RESOURCE_TYPES = [
  { value: 'ELECTRICITY', label: 'Электроснабжение' },
  { value: 'GAS', label: 'Газоснабжение' },
  { value: 'WATER', label: 'Водоснабжение' },
  { value: 'HEAT', label: 'Теплоснабжение' },
  { value: 'SEWAGE', label: 'Водоотведение' },
  { value: 'CONSTRUCTION', label: 'Строительство' },
  { value: 'OTHER', label: 'Прочее' },
]

const schema = z.object({
  client_name: z.string().min(1, 'Введите имя'),
  client_phone: z.string().min(1, 'Введите телефон'),
  address: z.string().min(1, 'Введите адрес'),
  resource_type: z.string().min(1, 'Выберите тип услуги'),
  client_email: z.string().email('Некорректный email').or(z.literal('')),
  comment: z.string(),
})

type FormValues = z.infer<typeof schema>

export function LeadForm() {
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const turnstileRef = useRef<TurnstileInstance | undefined>(undefined)

  const { mutateAsync, isPending, error } = useSubmitLeadForm()

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { resource_type: '', client_email: '', comment: '' },
  })

  const onSubmit = async (values: FormValues) => {
    if (!turnstileToken) return

    await mutateAsync({
      ...values,
      cf_turnstile_token: turnstileToken,
    })

    setSuccess(true)
    reset()
    setTurnstileToken(null)
    turnstileRef.current?.reset()
  }

  if (success) {
    return (
      <div className="rounded-lg border bg-card p-8 text-center shadow-sm">
        <div className="mb-2 text-4xl">✓</div>
        <h3 className="mb-2 text-lg font-semibold">Заявка принята!</h3>
        <p className="text-muted-foreground">Мы свяжемся с вами в ближайшее время.</p>
        <button
          onClick={() => setSuccess(false)}
          className="mt-4 text-sm text-primary hover:underline"
        >
          Отправить ещё одну заявку
        </button>
      </div>
    )
  }

  const submitError = (error as Error)?.message

  return (
    <div className="rounded-lg border bg-card p-6 shadow-sm">
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="client_name">Имя *</Label>
          <Input
            id="client_name"
            placeholder="Иван Иванов"
            {...register('client_name')}
            className={cn(errors.client_name && 'border-destructive')}
          />
          {errors.client_name && (
            <p className="text-xs text-destructive">{errors.client_name.message}</p>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="client_phone">Телефон *</Label>
          <Input
            id="client_phone"
            placeholder="+7 (999) 000-00-00"
            type="tel"
            {...register('client_phone')}
            className={cn(errors.client_phone && 'border-destructive')}
          />
          {errors.client_phone && (
            <p className="text-xs text-destructive">{errors.client_phone.message}</p>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="address">Адрес объекта *</Label>
          <Input
            id="address"
            placeholder="г. Москва, ул. Примерная, д. 1"
            {...register('address')}
            className={cn(errors.address && 'border-destructive')}
          />
          {errors.address && (
            <p className="text-xs text-destructive">{errors.address.message}</p>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="resource_type">Тип услуги *</Label>
          <select
            id="resource_type"
            {...register('resource_type')}
            className={cn(
              'flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
              errors.resource_type && 'border-destructive',
            )}
          >
            <option value="">Выберите услугу...</option>
            {RESOURCE_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
          {errors.resource_type && (
            <p className="text-xs text-destructive">{errors.resource_type.message}</p>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="client_email">Email (необязательно)</Label>
          <Input
            id="client_email"
            type="email"
            placeholder="ivan@example.com"
            {...register('client_email')}
            className={cn(errors.client_email && 'border-destructive')}
          />
          {errors.client_email && (
            <p className="text-xs text-destructive">{errors.client_email.message}</p>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="comment">Комментарий</Label>
          <textarea
            id="comment"
            {...register('comment')}
            rows={3}
            placeholder="Дополнительная информация..."
            className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 resize-none"
          />
        </div>

        <Turnstile
          ref={turnstileRef}
          siteKey={env.NEXT_PUBLIC_CF_TURNSTILE_SITE_KEY}
          onSuccess={setTurnstileToken}
          onExpire={() => setTurnstileToken(null)}
          options={{ size: 'invisible' }}
        />

        {submitError && (
          <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {submitError}
          </p>
        )}

        <Button
          type="submit"
          className="w-full"
          disabled={isPending || !turnstileToken}
          loading={isPending}
        >
          Отправить заявку
        </Button>
      </form>
    </div>
  )
}
