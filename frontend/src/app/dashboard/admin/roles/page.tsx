'use client'

import Link from 'next/link'
import { useCan } from '@/shared/auth/hooks'
import { useAdminRoles } from '@/shared/api/admin'

export default function AdminRolesPage() {
  const canRead = useCan('users.read')
  const { data, isLoading, error } = useAdminRoles()

  if (!canRead) {
    return (
      <div className="p-8 text-sm text-muted-foreground">
        Нет доступа к просмотру ролей.
      </div>
    )
  }

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-lg font-semibold">Роли</h1>
        <div className="mt-1 flex gap-4 text-sm">
          <Link href="/dashboard/admin" className="text-muted-foreground hover:text-foreground transition-colors">
            Пользователи
          </Link>
          <span className="font-medium text-foreground">Роли</span>
        </div>
      </div>

      {isLoading && <div className="text-sm text-muted-foreground">Загрузка...</div>}
      {error && <div className="text-sm text-destructive">Ошибка загрузки ролей</div>}

      {data && (
        <div className="space-y-4">
          {data.items.map((role) => (
            <div key={role.name} className="rounded-lg border border-border p-4">
              <div className="flex items-center gap-3 mb-2">
                <span className="font-medium text-sm">{role.name}</span>
                {role.description && (
                  <span className="text-xs text-muted-foreground">{role.description}</span>
                )}
              </div>
              <div className="flex flex-wrap gap-1">
                {role.permissions.length > 0
                  ? role.permissions.map((p) => (
                      <span
                        key={p}
                        className="inline-flex rounded px-2 py-0.5 text-xs font-mono bg-muted text-muted-foreground"
                      >
                        {p}
                      </span>
                    ))
                  : <span className="text-xs text-muted-foreground">Нет разрешений</span>
                }
              </div>
            </div>
          ))}
          {data.items.length === 0 && (
            <div className="text-sm text-muted-foreground">Роли не найдены</div>
          )}
        </div>
      )}
    </div>
  )
}
