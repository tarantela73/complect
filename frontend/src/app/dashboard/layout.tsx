'use client'

import { useEffect } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import Link from 'next/link'
import { useIsAuthenticated, useCan, useAuth } from '@/shared/auth/hooks'
import { logout } from '@/shared/auth/api'
import { cn } from '@/shared/ui/utils'

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
]

function NavLink({ item }: { item: NavItem }) {
  const canView = useCan(item.permission)
  const pathname = usePathname()
  const isActive = item.exact ? pathname === item.href : pathname.startsWith(item.href)

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
        <div className="flex h-14 items-center border-b border-sidebar-border px-4">
          <span className="text-sm font-semibold tracking-wide">ERP Комплект</span>
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
