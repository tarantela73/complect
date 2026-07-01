import { useQuery } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

export type UserListItem = components['schemas']['UserListItemSchema']
export type UserListResponse = components['schemas']['UserListResponse']

export const userKeys = {
  all: ['users'] as const,
  lists: () => [...userKeys.all, 'list'] as const,
}

export function useUsers() {
  return useQuery({
    queryKey: userKeys.lists(),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/admin/users/')
      if (error) throw new Error('Ошибка загрузки пользователей')
      return data as UserListResponse
    },
  })
}
