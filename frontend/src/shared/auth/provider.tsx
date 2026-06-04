'use client'

import { useEffect, useRef } from 'react'
import { configureAuthInterceptors } from '@/shared/api/client'
import { refreshToken } from './api'
import { useAuthStore } from './store'

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const { accessToken, updateAccessToken, clearAuth } = useAuthStore()
  const refreshingRef = useRef<Promise<string | null> | null>(null)

  useEffect(() => {
    configureAuthInterceptors(
      () => useAuthStore.getState().accessToken,
      async () => {
        if (refreshingRef.current) return refreshingRef.current

        refreshingRef.current = refreshToken()
          .then((data) => {
            updateAccessToken(data.access_token)
            return data.access_token
          })
          .catch(() => {
            clearAuth()
            return null
          })
          .finally(() => {
            refreshingRef.current = null
          })

        return refreshingRef.current
      },
    )
  }, [updateAccessToken, clearAuth])

  return <>{children}</>
}
