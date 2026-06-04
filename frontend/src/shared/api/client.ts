import createClient, { type Middleware } from 'openapi-fetch'
import type { paths } from './generated/schema'
import { env } from '@/shared/config/env'

export const apiClient = createClient<paths>({
  baseUrl: env.NEXT_PUBLIC_API_URL,
  credentials: 'include',
})

let _getAccessToken: (() => string | null) | null = null
let _onUnauthorized: (() => Promise<string | null>) | null = null

export function configureAuthInterceptors(
  getToken: () => string | null,
  onUnauthorized: () => Promise<string | null>,
): void {
  _getAccessToken = getToken
  _onUnauthorized = onUnauthorized
}

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = _getAccessToken?.()
    if (token) {
      request.headers.set('Authorization', `Bearer ${token}`)
    }
    return request
  },

  async onResponse({ response, request }) {
    if (response.status === 401 && _onUnauthorized) {
      const newToken = await _onUnauthorized()
      if (newToken) {
        const retried = request.clone()
        retried.headers.set('Authorization', `Bearer ${newToken}`)
        return fetch(retried)
      }
    }
    return response
  },
}

apiClient.use(authMiddleware)
