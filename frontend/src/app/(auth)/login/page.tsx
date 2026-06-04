'use client'

import { useRouter } from 'next/navigation'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useState } from 'react'
import { Button } from '@/shared/ui/button'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'
import { cn } from '@/shared/ui/utils'
import { login } from '@/shared/auth/api'
import { useAuthStore } from '@/shared/auth/store'

const loginSchema = z.object({
  username: z.string().min(1, 'Введите логин'),
  password: z.string().min(1, 'Введите пароль'),
})

type LoginFormValues = z.infer<typeof loginSchema>

export default function LoginPage() {
  const router = useRouter()
  const { setAccessToken, setMfaPending } = useAuthStore()
  const [error, setError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
  })

  const onSubmit = async (values: LoginFormValues) => {
    setError(null)

    try {
      const data = await login(values.username, values.password)

      if (data.mfa_required) {
        setMfaPending(data.access_token)
        router.push('/login/mfa')
        return
      }

      setAccessToken(data.access_token, data.permissions ?? [])
      router.push('/dashboard')
    } catch (err) {
      const code = (err as Error & { code?: string }).code
      if (code === 'INVALID_CREDENTIALS') {
        setError('Неверный логин или пароль.')
      } else {
        setError('Ошибка входа. Попробуйте позже.')
      }
    }
  }

  return (
    <div className="w-full max-w-sm space-y-6">
      <div className="space-y-2 text-center">
        <h1 className="text-2xl font-semibold tracking-tight">ERP Комплект</h1>
        <p className="text-sm text-muted-foreground">Войдите в систему</p>
      </div>

      <div className="rounded-lg border bg-card p-6 shadow-sm">
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="username">Логин</Label>
            <Input
              id="username"
              autoComplete="username"
              autoFocus
              {...register('username')}
              className={cn(errors.username && 'border-destructive')}
            />
            {errors.username && (
              <p className="text-xs text-destructive">{errors.username.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="password">Пароль</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              {...register('password')}
              className={cn(errors.password && 'border-destructive')}
            />
            {errors.password && (
              <p className="text-xs text-destructive">{errors.password.message}</p>
            )}
          </div>

          {error && (
            <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </p>
          )}

          <Button type="submit" className="w-full" loading={isSubmitting}>
            Войти
          </Button>
        </form>
      </div>
    </div>
  )
}
