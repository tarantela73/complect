'use client'

import { usePageRevisions, useRollbackRevision } from '@/shared/api/cms'
import { RevisionItem } from './RevisionItem'

interface RevisionHistoryDrawerProps {
  slug: string
  isOpen: boolean
  onClose: () => void
  canWrite: boolean
  onRollbackSuccess?: () => void
}

export function RevisionHistoryDrawer({
  slug,
  isOpen,
  onClose,
  canWrite,
  onRollbackSuccess,
}: RevisionHistoryDrawerProps) {
  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading } = usePageRevisions(slug)
  const { mutate: rollback, isPending: isRollingBack } = useRollbackRevision(slug)

  const allRevisions = data?.pages.flatMap((page) => page.results) ?? []

  function handleRollback(revisionId: string) {
    rollback(revisionId, {
      onSuccess: () => {
        onRollbackSuccess?.()
        onClose()
      },
    })
  }

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 z-40 bg-black/30 transition-opacity duration-200 ${
          isOpen ? 'opacity-100' : 'pointer-events-none opacity-0'
        }`}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer */}
      <div
        className={`fixed right-0 top-0 z-50 flex h-full w-80 flex-col bg-background shadow-xl transition-transform duration-200 ${
          isOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b px-4 py-3">
          <h2 className="text-sm font-semibold">История версий</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="Закрыть"
          >
            ✕
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-4">
          {isLoading && (
            <p className="py-6 text-center text-sm text-muted-foreground">Загрузка...</p>
          )}

          {!isLoading && allRevisions.length === 0 && (
            <p className="py-6 text-center text-sm text-muted-foreground">
              История версий пуста
            </p>
          )}

          {allRevisions.map((revision) => (
            <RevisionItem
              key={revision.id}
              revision={revision}
              canWrite={canWrite}
              isRollingBack={isRollingBack}
              onRollback={handleRollback}
            />
          ))}

          {hasNextPage && (
            <div className="py-3 text-center">
              <button
                type="button"
                onClick={() => fetchNextPage()}
                disabled={isFetchingNextPage}
                className="rounded-md border px-4 py-2 text-sm hover:bg-muted disabled:opacity-50"
              >
                {isFetchingNextPage ? 'Загрузка...' : 'Загрузить ещё'}
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
