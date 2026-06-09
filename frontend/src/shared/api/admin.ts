import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

export type UserListItem = components['schemas']['UserListItemSchema']
export type UserListResponse = components['schemas']['UserListResponse']
export type CreateUserRequest = components['schemas']['CreateUserRequest']
export type CreateUserResponse = components['schemas']['CreateUserResponse']
export type AssignRolesRequest = components['schemas']['AssignRolesRequest']
export type RoleDetail = components['schemas']['RoleDetailSchema']
export type RoleListResponse = components['schemas']['RoleListResponse']

type ApiErrorBody = { error?: { code?: string; message?: string } }
const apiMsg = (error: unknown, fallback: string) =>
  (error as ApiErrorBody)?.error?.message ?? fallback
const apiCode = (error: unknown) => (error as ApiErrorBody)?.error?.code ?? null

export class AdminApiError extends Error {
  constructor(
    public readonly code: string | null,
    message: string,
  ) {
    super(message)
  }
}

export const adminKeys = {
  users: () => ['admin', 'users'] as const,
  roles: () => ['admin', 'roles'] as const,
}

export function useAdminUsers() {
  return useQuery({
    queryKey: adminKeys.users(),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/admin/users/')
      if (error) throw new AdminApiError(apiCode(error), apiMsg(error, 'Ошибка загрузки пользователей'))
      return data as UserListResponse
    },
  })
}

export function useAdminRoles() {
  return useQuery({
    queryKey: adminKeys.roles(),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/admin/roles/')
      if (error) throw new AdminApiError(apiCode(error), apiMsg(error, 'Ошибка загрузки ролей'))
      return data as RoleListResponse
    },
  })
}

export function useCreateUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: CreateUserRequest) => {
      const { data, error } = await apiClient.POST('/api/v1/admin/users/', { body })
      if (error) throw new AdminApiError(apiCode(error), apiMsg(error, 'Ошибка создания пользователя'))
      return data as CreateUserResponse
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.users() }),
  })
}

export function useDisableUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (userId: number) => {
      const { error } = await apiClient.POST('/api/v1/admin/users/{user_id}/disable/', {
        params: { path: { user_id: userId } },
      })
      if (error) throw new AdminApiError(apiCode(error), apiMsg(error, 'Ошибка отключения пользователя'))
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.users() }),
  })
}

export function useEnableUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (userId: number) => {
      const { error } = await apiClient.POST('/api/v1/admin/users/{user_id}/enable/', {
        params: { path: { user_id: userId } },
      })
      if (error) throw new AdminApiError(apiCode(error), apiMsg(error, 'Ошибка включения пользователя'))
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.users() }),
  })
}

export function useAssignRoles() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ userId, roles }: { userId: number; roles: string[] }) => {
      const { error } = await apiClient.POST('/api/v1/admin/users/{user_id}/roles/', {
        params: { path: { user_id: userId } },
        body: { roles },
      })
      if (error) throw new AdminApiError(apiCode(error), apiMsg(error, 'Ошибка назначения ролей'))
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.users() }),
  })
}
