import { useMutation } from '@tanstack/react-query'
import type { components } from './generated/schema'

export type PublicLeadFormRequest = components['schemas']['PublicLeadFormRequest']
export type PublicLeadFormResponse = components['schemas']['PublicLeadFormResponse']
export type PublicPageContentResponse = components['schemas']['PublicPageContentResponse']

type ApiErrorBody = { error?: { message?: string; code?: string } }

export function useSubmitLeadForm() {
  return useMutation({
    mutationFn: async (body: PublicLeadFormRequest): Promise<PublicLeadFormResponse> => {
      const res = await fetch('/api/v1/public/requests/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const err = (await res.json().catch(() => ({}))) as ApiErrorBody
        const msg = err?.error?.message ?? 'Ошибка отправки заявки. Попробуйте позже.'
        const error = new Error(msg) as Error & { code?: string }
        error.code = err?.error?.code
        throw error
      }
      return res.json() as Promise<PublicLeadFormResponse>
    },
  })
}
