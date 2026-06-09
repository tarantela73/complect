'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useCan } from '@/shared/auth/hooks'
import {
  useAdminUsers,
  useAdminRoles,
  useCreateUser,
  useDisableUser,
  useEnableUser,
  useAssignRoles,
  AdminApiError,
  type UserListItem,
  type RoleDetail,
} from '@/shared/api/admin'
import { Button } from '@/shared/ui/button'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'

function ActiveBadge({ active }: { active: boolean }) {
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
        active
          ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
          : 'bg-muted text-muted-foreground'
      }`}
    >
      {active ? 'Активен' : 'Отключён'}
    </span>
  )
}

function RoleBadge({ role }: { role: string }) {
  const colors: Record<string, string> = {
    admin: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
    manager: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
    viewer: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400',
  }
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${colors[role] ?? 'bg-muted text-muted-foreground'}`}>
      {role}
    </span>
  )
}

interface AssignRolesModalProps {
  user: UserListItem
  availableRoles: RoleDetail[]
  onClose: () => void
}

function AssignRolesModal({ user, availableRoles, onClose }: AssignRolesModalProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set(user.roles))
  const [error, setError] = useState<string | null>(null)
  const assignRoles = useAssignRoles()

  const toggle = (role: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(role)) next.delete(role)
      else next.add(role)
      return next
    })
  }

  const handleSave = async () => {
    setError(null)
    try {
      await assignRoles.mutateAsync({ userId: user.id, roles: [...selected] })
      onClose()
    } catch (err) {
      setError(err instanceof AdminApiError ? err.message : 'Ошибка назначения ролей')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-80 rounded-lg border border-border bg-background p-6 shadow-lg">
        <h2 className="mb-4 text-sm font-semibold">Роли: {user.email}</h2>

        <div className="space-y-2 mb-4">
          {availableRoles.map((r) => (
            <label key={r.name} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={selected.has(r.name)}
                onChange={() => toggle(r.name)}
                className="rounded"
              />
              <span className="text-sm">{r.name}</span>
              {r.description && (
                <span className="text-xs text-muted-foreground">— {r.description}</span>
              )}
            </label>
          ))}
        </div>

        {error && <p className="mb-3 text-xs text-destructive">{error}</p>}

        <div className="flex gap-2 justify-end">
          <Button variant="outline" size="sm" onClick={onClose}>Отмена</Button>
          <Button size="sm" onClick={handleSave} disabled={assignRoles.isPending}>
            {assignRoles.isPending ? 'Сохранение...' : 'Сохранить'}
          </Button>
        </div>
      </div>
    </div>
  )
}

interface CreateUserModalProps {
  availableRoles: RoleDetail[]
  onClose: (tempPassword?: string) => void
}

function CreateUserModal({ availableRoles, onClose }: CreateUserModalProps) {
  const [email, setEmail] = useState('')
  const [fullName, setFullName] = useState('')
  const [roles, setRoles] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const createUser = useCreateUser()

  const toggleRole = (role: string) => {
    setRoles((prev) => {
      const next = new Set(prev)
      if (next.has(role)) next.delete(role)
      else next.add(role)
      return next
    })
  }

  const handleCreate = async () => {
    setError(null)
    if (!email.trim()) {
      setError('Email обязателен')
      return
    }
    if (!fullName.trim()) {
      setError('Имя обязательно')
      return
    }
    try {
      const result = await createUser.mutateAsync({
        email: email.trim(),
        full_name: fullName.trim(),
        roles: [...roles],
        send_invite_email: false,
      })
      onClose(result.temp_password)
    } catch (err) {
      setError(err instanceof AdminApiError ? err.message : 'Ошибка создания пользователя')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-96 rounded-lg border border-border bg-background p-6 shadow-lg">
        <h2 className="mb-4 text-sm font-semibold">Новый пользователь</h2>

        <div className="space-y-4 mb-4">
          <div className="space-y-1">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="user@example.com"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="fullName">Имя</Label>
            <Input
              id="fullName"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Иван Иванов"
            />
          </div>
          <div className="space-y-1">
            <Label>Роли</Label>
            <div className="space-y-1">
              {availableRoles.map((r) => (
                <label key={r.name} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={roles.has(r.name)}
                    onChange={() => toggleRole(r.name)}
                    className="rounded"
                  />
                  <span className="text-sm">{r.name}</span>
                </label>
              ))}
            </div>
          </div>
        </div>

        {error && <p className="mb-3 text-xs text-destructive">{error}</p>}

        <div className="flex gap-2 justify-end">
          <Button variant="outline" size="sm" onClick={() => onClose()}>Отмена</Button>
          <Button size="sm" onClick={handleCreate} disabled={createUser.isPending}>
            {createUser.isPending ? 'Создание...' : 'Создать'}
          </Button>
        </div>
      </div>
    </div>
  )
}

function TempPasswordModal({ password, onClose }: { password: string; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-96 rounded-lg border border-border bg-background p-6 shadow-lg">
        <h2 className="mb-2 text-sm font-semibold">Пользователь создан</h2>
        <p className="mb-3 text-xs text-muted-foreground">
          Сохраните временный пароль — он показывается только один раз.
        </p>
        <div className="mb-4 rounded bg-muted p-3 font-mono text-sm break-all">{password}</div>
        <div className="flex justify-end">
          <Button size="sm" onClick={onClose}>Закрыть</Button>
        </div>
      </div>
    </div>
  )
}

interface UserRowProps {
  user: UserListItem
  canWrite: boolean
  roles: RoleDetail[]
  onAssignRoles: (user: UserListItem) => void
  onToggle: (user: UserListItem) => void
}

function UserRow({ user, canWrite, roles, onAssignRoles, onToggle }: UserRowProps) {
  return (
    <tr className="border-b border-border hover:bg-muted/30 transition-colors">
      <td className="px-4 py-3 text-sm font-medium">{user.email}</td>
      <td className="px-4 py-3 text-sm text-muted-foreground">{user.full_name}</td>
      <td className="px-4 py-3">
        <div className="flex flex-wrap gap-1">
          {user.roles.length > 0
            ? user.roles.map((r) => <RoleBadge key={r} role={r} />)
            : <span className="text-xs text-muted-foreground">—</span>
          }
        </div>
      </td>
      <td className="px-4 py-3"><ActiveBadge active={user.is_active} /></td>
      <td className="px-4 py-3 text-xs text-muted-foreground">
        {user.last_login ? new Date(user.last_login).toLocaleDateString('ru-RU') : '—'}
      </td>
      {canWrite && (
        <td className="px-4 py-3">
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => onAssignRoles(user)}>
              Роли
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => onToggle(user)}
            >
              {user.is_active ? 'Откл.' : 'Вкл.'}
            </Button>
          </div>
        </td>
      )}
    </tr>
  )
}

export default function AdminUsersPage() {
  const canRead = useCan('users.read')
  const canWrite = useCan('users.write')
  const canCreate = useCan('users.create')

  const { data: usersData, isLoading, error } = useAdminUsers()
  const { data: rolesData } = useAdminRoles()

  const disableUser = useDisableUser()
  const enableUser = useEnableUser()

  const [assignTarget, setAssignTarget] = useState<UserListItem | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [tempPassword, setTempPassword] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const roles = rolesData?.items ?? []

  const handleToggle = async (user: UserListItem) => {
    setActionError(null)
    try {
      if (user.is_active) {
        await disableUser.mutateAsync(user.id)
      } else {
        await enableUser.mutateAsync(user.id)
      }
    } catch (err) {
      setActionError(err instanceof AdminApiError ? err.message : 'Ошибка')
    }
  }

  if (!canRead) {
    return (
      <div className="p-8 text-sm text-muted-foreground">
        Нет доступа к управлению пользователями.
      </div>
    )
  }

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Пользователи</h1>
          <div className="mt-1 flex gap-4 text-sm">
            <span className="font-medium text-foreground">Список</span>
            <Link href="/dashboard/admin/roles" className="text-muted-foreground hover:text-foreground transition-colors">
              Роли
            </Link>
          </div>
        </div>
        {canCreate && (
          <Button size="sm" onClick={() => setShowCreate(true)}>
            + Новый пользователь
          </Button>
        )}
      </div>

      {actionError && (
        <div className="mb-4 rounded border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {actionError}
        </div>
      )}

      {isLoading && <div className="text-sm text-muted-foreground">Загрузка...</div>}
      {error && <div className="text-sm text-destructive">Ошибка загрузки пользователей</div>}

      {usersData && (
        <div className="rounded-lg border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-muted/50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Email</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Имя</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Роли</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Статус</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Последний вход</th>
                {canWrite && <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Действия</th>}
              </tr>
            </thead>
            <tbody>
              {usersData.items.map((user) => (
                <UserRow
                  key={user.id}
                  user={user}
                  canWrite={canWrite}
                  roles={roles}
                  onAssignRoles={setAssignTarget}
                  onToggle={handleToggle}
                />
              ))}
              {usersData.items.length === 0 && (
                <tr>
                  <td colSpan={canWrite ? 6 : 5} className="px-4 py-8 text-center text-sm text-muted-foreground">
                    Пользователей нет
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {assignTarget && (
        <AssignRolesModal
          user={assignTarget}
          availableRoles={roles}
          onClose={() => setAssignTarget(null)}
        />
      )}

      {showCreate && (
        <CreateUserModal
          availableRoles={roles}
          onClose={(pwd) => {
            setShowCreate(false)
            if (pwd) setTempPassword(pwd)
          }}
        />
      )}

      {tempPassword && (
        <TempPasswordModal
          password={tempPassword}
          onClose={() => setTempPassword(null)}
        />
      )}
    </div>
  )
}
