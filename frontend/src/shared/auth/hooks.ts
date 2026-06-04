'use client'

import { useAuthStore } from './store'

export function useAuth() {
  return useAuthStore()
}

export function useIsAuthenticated(): boolean {
  return useAuthStore((s) => s.isAuthenticated)
}

export function useCan(permission: string): boolean {
  const permissions = useAuthStore((s) => s.permissions)
  return permissions.includes(permission)
}

export function useCanAny(permissions: string[]): boolean {
  const stored = useAuthStore((s) => s.permissions)
  return permissions.some((p) => stored.includes(p))
}

export function useCurrentUser() {
  return useAuthStore((s) => ({
    userId: s.userId,
    tenantId: s.tenantId,
    username: s.username,
  }))
}
