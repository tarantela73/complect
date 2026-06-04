import { env } from '@/shared/config/env'

export interface LoginResponse {
  access_token: string
  token_type: string
  mfa_required: boolean
  permissions: string[]
}

export interface RefreshResponse {
  access_token: string
  token_type: string
  mfa_required: boolean
  permissions: string[]
}

const BASE = env.NEXT_PUBLIC_API_URL

async function parseResponse<T>(res: Response): Promise<T> {
  const body = await res.json()
  if (!res.ok) {
    const code = body?.error?.code ?? 'UNKNOWN_ERROR'
    const message = body?.error?.message ?? `HTTP ${res.status}`
    const err = new Error(message)
    ;(err as Error & { code: string }).code = code
    throw err
  }
  return body as T
}

export async function login(
  username: string,
  password: string,
  deviceFingerprint = '',
): Promise<LoginResponse> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ username, password, device_fingerprint: deviceFingerprint }),
  })
  return parseResponse<LoginResponse>(res)
}

export async function refreshToken(): Promise<RefreshResponse> {
  const res = await fetch(`${BASE}/auth/refresh`, {
    method: 'POST',
    credentials: 'include',
  })
  return parseResponse<RefreshResponse>(res)
}

export async function logout(): Promise<void> {
  await fetch(`${BASE}/auth/logout`, {
    method: 'POST',
    credentials: 'include',
  })
}

export async function verifyMfa(
  partialToken: string,
  totpCode: string,
): Promise<LoginResponse> {
  const res = await fetch(`${BASE}/auth/mfa/verify`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${partialToken}`,
    },
    credentials: 'include',
    body: JSON.stringify({ totp_code: totpCode }),
  })
  return parseResponse<LoginResponse>(res)
}
