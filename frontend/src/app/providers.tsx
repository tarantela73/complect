'use client'

import { ProgressProvider } from '@bprogress/next/app'
import { QueryClientProvider } from '@tanstack/react-query'
import { ReactQueryDevtools } from '@tanstack/react-query-devtools'
import { queryClient } from '@/shared/api/query-client'
import { AuthProvider } from '@/shared/auth/provider'

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>{children}</AuthProvider>
      <ReactQueryDevtools initialIsOpen={false} />
      <ProgressProvider height="2px" color="#2563eb" options={{ showSpinner: false }} shallowRouting />
    </QueryClientProvider>
  )
}
