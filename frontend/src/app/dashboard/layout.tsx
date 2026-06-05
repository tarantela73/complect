'use client'

import { useEffect } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import Link from 'next/link'
import { useIsAuthenticated, useCan, useAuth } from '@/shared/auth/hooks'
import { logout } from '@/shared/auth/api'
import { cn } from '@/shared/ui/utils'
import { useUnreadCount, useNotificationSocket } from '@/shared/api/notifications'

interface NavItem {
  href: string
  label: string
  permission: string
  exact?: boolean
}

const NAV_ITEMS: NavItem[] = [
  { href: '/dashboard', label: 'Главная', permission: 'dashboard.read', exact: true },
  { href: '/dashboard/contragents', label: 'Контрагенты', permission: 'contragents.read' },
  { href: '/dashboard/contracts', label: 'Договоры', permission: 'contracts.read' },
  { href: '/dashboard/finance', label: 'Финансы', permission: 'finance.read' },
  { href: '/dashboard/files', label: 'Файлы', permission: 'files.read' },
  { href: '/dashboard/objects', label: 'Объекты', permission: 'objects.read' },
  { href: '/dashboard/notifications', label: 'Уведомления', permission: 'notifications.read' },
]

function NotificationBell() {
  const canRead = useCan('notifications.read')
  const { data } = useUnreadCount()
  useNotificationSocket()

  if (!canRead) return null

  const count = data?.count ?? 0

  return (
    <Link
      href="/dashboard/notifications"
      className="relative flex h-8 w-8 items-center justify-center rounded-md text-sidebar-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground transition-colors"
      title="Уведомления"
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
        <path d="M13.73 21a2 2 0 0 1-3.46 0" />
      </svg>
      {count > 0 && (
        <span className="absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-destructive text-[10px] font-bold text-destructive-foreground">
          {count > 9 ? '9+' : count}
        </span>
      )}
    </Link>
  )
}

function NavLink({ item }: { item: NavItem }) {
  const canView = useCan(item.permission)
  const pathname = usePathname()
  const isActive = item.exact ? pathname === item.href : (pathname?.startsWith(item.href) ?? false)

  if (!canView) return null

  return (
    <Link
      href={item.href}
      className={cn(
        'flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors',
        isActive
          ? 'bg-sidebar-accent text-sidebar-foreground'
          : 'text-sidebar-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground',
      )}
    >
      {item.label}
    </Link>
  )
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const isAuthenticated = useIsAuthenticated()
  const { username, clearAuth } = useAuth()

  useEffect(() => {
    if (!isAuthenticated) {
      router.replace('/login')
    }
  }, [isAuthenticated, router])

  if (!isAuthenticated) return null

  const handleLogout = async () => {
    await logout().catch(() => null)
    clearAuth()
    router.replace('/login')
  }

  return (
    <div className="flex h-screen bg-background">
      <aside className="flex w-56 flex-col bg-sidebar text-sidebar-foreground">
        <div className="flex h-14 items-center justify-between border-b border-sidebar-border px-4">
          <span className="text-sm font-semibold tracking-wide">ERP Комплект</span>
          <NotificationBell />
        </div>

        <nav className="flex-1 space-y-1 p-3">
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.href} item={item} />
          ))}
        </nav>

        <div className="border-t border-sidebar-border p-3">
          <div className="mb-2 px-3 py-1 text-xs text-sidebar-muted-foreground truncate">
            {username ?? '—'}
          </div>
          <button
            onClick={handleLogout}
            className="w-full rounded-md px-3 py-2 text-left text-sm text-sidebar-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground transition-colors"
          >
            Выйти
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-auto">
        {children}
      </main>
    </div>
  )
}
