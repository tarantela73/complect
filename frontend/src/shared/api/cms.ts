import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

export type CmsPageContentResponse = components['schemas']['CmsPageContentResponse']
export type CmsPageUpdateRequest = components['schemas']['CmsPageUpdateRequest']
export type CmsImageResponse = components['schemas']['CmsImageResponse']
export type CmsImageListResponse = components['schemas']['CmsImageListResponse']
export type TiptapDocSchema = components['schemas']['TiptapDocSchema']

export const cmsKeys = {
  all: ['cms'] as const,
  pages: () => [...cmsKeys.all, 'pages'] as const,
  page: (slug: string) => [...cmsKeys.pages(), slug] as const,
  gallery: (section?: string) => [...cmsKeys.all, 'gallery', section ?? 'all'] as const,
}

export function useCmsPage(slug: string) {
  return useQuery({
    queryKey: cmsKeys.page(slug),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/cms/pages/{slug}/', {
        params: { path: { slug } },
      })
      if (error) throw error
      return data
    },
  })
}

export function useSaveCmsPage(slug: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: CmsPageUpdateRequest) => {
      const { data, error } = await apiClient.PATCH('/api/v1/cms/pages/{slug}/', {
        params: { path: { slug } },
        body,
      })
      if (error) throw error
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: cmsKeys.page(slug) })
    },
  })
}

export function useCmsGallery(section?: string) {
  return useQuery({
    queryKey: cmsKeys.gallery(section),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/cms/gallery/', {
        params: { query: section ? { section } : {} },
      })
      if (error) throw error
      return data
    },
  })
}

export function usePublishCmsPage(slug: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const { data, error } = await apiClient.POST('/api/v1/cms/pages/{slug}/publish/', {
        params: { path: { slug } },
      })
      if (error) throw error
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: cmsKeys.page(slug) })
    },
  })
}

export function useCreateGalleryImage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (params: { section: string; s3_key: string; caption?: string; order?: number }) => {
      const { data, error } = await apiClient.POST('/api/v1/cms/gallery/', {
        params: { query: params },
      })
      if (error) throw error
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: cmsKeys.gallery() })
    },
  })
}
