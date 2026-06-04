import { create } from 'zustand'
import { jwtDecode } from 'jwt-decode'

interface JwtPayload {
  user_id: string
  tenant_id: string
  username: string
  exp: number
  type: string
}

interface AuthStore {
  accessToken: string | null
  userId: string | null
  tenantId: string | null
  username: string | null
  permissions: string[]
  mfaPending: boolean
  partialToken: string | null

  isAuthenticated: boolean

  setAccessToken: (token: string, permissions: string[]) => void
  setMfaPending: (partialToken: string) => void
  clearAuth: () => void
  updateAccessToken: (token: string) => void
}

export const useAuthStore = create<AuthStore>((set) => ({
  accessToken: null,
  userId: null,
  tenantId: null,
  username: null,
  permissions: [],
  mfaPending: false,
  partialToken: null,
  isAuthenticated: false,

  setAccessToken: (token, permissions) => {
    let userId: string | null = null
    let tenantId: string | null = null
    let username: string | null = null

    try {
      const payload = jwtDecode<JwtPayload>(token)
      userId = payload.user_id
      tenantId = payload.tenant_id
      username = payload.username
    } catch {
      // malformed token — clear state
    }

    set({
      accessToken: token,
      userId,
      tenantId,
      username,
      permissions,
      mfaPending: false,
      partialToken: null,
      isAuthenticated: true,
    })
  },

  setMfaPending: (partialToken) => {
    set({
      mfaPending: true,
      partialToken,
      isAuthenticated: false,
    })
  },

  clearAuth: () => {
    set({
      accessToken: null,
      userId: null,
      tenantId: null,
      username: null,
      permissions: [],
      mfaPending: false,
      partialToken: null,
      isAuthenticated: false,
    })
  },

  updateAccessToken: (token) => {
    set((state) => {
      try {
        const payload = jwtDecode<JwtPayload>(token)
        return {
          accessToken: token,
          userId: payload.user_id,
          tenantId: payload.tenant_id,
          username: payload.username,
          isAuthenticated: true,
          permissions: state.permissions,
        }
      } catch {
        return {}
      }
    })
  },
}))
