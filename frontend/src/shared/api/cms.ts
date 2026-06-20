import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

export type CmsPageContentResponse = components['schemas']['CmsPageContentResponse']
export type CmsPageUpdateRequest = components['schemas']['CmsPageUpdateRequest']
export type CmsImageResponse = components['schemas']['CmsImageResponse']
export type CmsImageListResponse = components['schemas']['CmsImageListResponse']
export type TiptapDocSchema = components['schemas']['TiptapDocSchema']
export type RevisionResponse = components['schemas']['RevisionResponse']

export const cmsKeys = {
  all: ['cms'] as const,
  pages: () => [...cmsKeys.all, 'pages'] as const,
  page: (slug: string) => [...cmsKeys.pages(), slug] as const,
  gallery: (section?: string) => [...cmsKeys.all, 'gallery', section ?? 'all'] as const,
  revisions: (slug: string) => [...cmsKeys.all, 'revisions', slug] as const,
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
      qc.invalidateQueries({ queryKey: cmsKeys.revisions(slug) })
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

export function useDeleteGalleryImage(section?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (imageId: string) => {
      const { error } = await apiClient.DELETE('/api/v1/cms/gallery/{image_id}/', {
        params: { path: { image_id: imageId } },
      })
      if (error) throw error
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: cmsKeys.gallery(section) }),
  })
}

export function useUpdateGalleryImage(section?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ imageId, body }: { imageId: string; body: components['schemas']['GalleryUpdateRequest'] }) => {
      const { data, error } = await apiClient.PATCH('/api/v1/cms/gallery/{image_id}/', {
        params: { path: { image_id: imageId } },
        body,
      })
      if (error) throw error
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: cmsKeys.gallery(section) }),
  })
}

export function useReorderGallery() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (items: components['schemas']['GalleryReorderItem'][]) => {
      const { data, error } = await apiClient.POST('/api/v1/cms/gallery/reorder/', {
        body: { items },
      })
      if (error) throw error
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: cmsKeys.gallery() }),
  })
}

export function useCmsGalleryUpload(section: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ file, caption = '', order = 0 }: { file: File; caption?: string; order?: number }) => {
      // 1. Request presigned URL via query params
      const { data: presigned, error: err1 } = await apiClient.POST('/api/v1/cms/gallery/request-upload/', {
        params: {
          query: {
            filename: file.name,
            content_type: file.type || 'application/octet-stream',
          },
        },
      })
      if (err1) throw err1

      // 2. Upload directly to S3 (skip stub URLs)
      if (!presigned.upload_url.startsWith('/stub/')) {
        const res = await fetch(presigned.upload_url, {
          method: 'PUT',
          body: file,
          headers: { 'Content-Type': file.type || 'application/octet-stream' },
        })
        if (!res.ok) throw new Error(`Upload failed: ${res.status}`)
      }

      // 3. Confirm upload
      const { data, error: err2 } = await apiClient.POST('/api/v1/cms/gallery/confirm-upload/', {
        body: { s3_key: presigned.s3_key, section, caption, alt: '', order },
      })
      if (err2) throw err2
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: cmsKeys.gallery(section) }),
  })
}

export function usePageRevisions(slug: string) {
  return useInfiniteQuery({
    queryKey: cmsKeys.revisions(slug),
    queryFn: async ({ pageParam }) => {
      const { data, error } = await apiClient.GET('/api/v1/cms/pages/{slug}/revisions/', {
        params: {
          path: { slug },
          query: pageParam !== undefined ? { cursor: pageParam } : {},
        },
      })
      if (error) throw error
      return data
    },
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
}

export function useRollbackRevision(slug: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (revisionId: string) => {
      const { data, error } = await apiClient.POST(
        '/api/v1/cms/pages/{slug}/revisions/{revision_id}/rollback/',
        {
          params: { path: { slug, revision_id: revisionId } },
        },
      )
      if (error) throw error
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: cmsKeys.page(slug) })
      qc.invalidateQueries({ queryKey: cmsKeys.revisions(slug) })
    },
  })
}
