import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

export type Attachment = components['schemas']['AttachmentResponse']
export type AttachmentList = components['schemas']['AttachmentListResponse']

type ApiErrorBody = { error?: { message?: string } }
const apiMsg = (error: unknown, fallback: string) =>
  (error as ApiErrorBody)?.error?.message ?? fallback

export const attachmentKeys = {
  all: ['attachments'] as const,
  list: (contractId: string) => [...attachmentKeys.all, 'list', contractId] as const,
}

export function useContractAttachments(contractId: string) {
  return useQuery({
    queryKey: attachmentKeys.list(contractId),
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        '/api/v1/contracts/{contract_id}/attachments/',
        { params: { path: { contract_id: contractId } } },
      )
      if (error) throw new Error('Ошибка загрузки вложений')
      return data as AttachmentList
    },
    enabled: !!contractId,
  })
}

export function useAttachFile(contractId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ fileId, attachmentType = 'OTHER' }: { fileId: string; attachmentType?: string }) => {
      const { error, response } = await apiClient.POST(
        '/api/v1/contracts/{contract_id}/attachments/',
        {
          params: { path: { contract_id: contractId } },
          body: { file_id: fileId, attachment_type: attachmentType },
          headers: { 'Idempotency-Key': crypto.randomUUID() },
        },
      )
      if (response.status === 422) throw new Error(apiMsg(error, 'Файл недоступен для прикрепления'))
      if (response.status === 403) throw new Error(apiMsg(error, 'Доступ запрещён'))
      if (error) throw new Error(apiMsg(error, 'Ошибка прикрепления файла'))
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: attachmentKeys.list(contractId) }),
  })
}

export function useDetachFile(contractId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (fileId: string) => {
      const { error, response } = await apiClient.DELETE(
        '/api/v1/contracts/{contract_id}/attachments/{file_id}/',
        {
          params: { path: { contract_id: contractId, file_id: fileId } },
          headers: { 'Idempotency-Key': crypto.randomUUID() },
        },
      )
      if (response.status === 422) throw new Error(apiMsg(error, 'Удаление заблокировано'))
      if (response.status !== 204 && error) throw new Error(apiMsg(error, 'Ошибка открепления файла'))
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: attachmentKeys.list(contractId) }),
  })
}
