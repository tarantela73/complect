'use client'

import { useState, useCallback } from 'react'

export interface MetaFields {
  title?: string
  description?: string
  canonical?: string
  og_image?: string
}

interface MetaEditorProps {
  meta: MetaFields
  overrides?: Record<string, boolean>
  onChange: (fields: MetaFields) => void
  onSave: () => void
  isSaving?: boolean
}

export function MetaEditor({ meta, overrides = {}, onChange, onSave, isSaving }: MetaEditorProps) {
  const [expanded, setExpanded] = useState(false)

  const handleChange = useCallback(
    (field: keyof MetaFields, value: string) => {
      onChange({ ...meta, [field]: value || undefined })
    },
    [meta, onChange],
  )

  return (
    <div className="rounded-lg border bg-card shadow-sm">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between p-4 text-left text-sm font-medium hover:bg-muted/50"
      >
        <span>SEO Meta</span>
        <span className="text-muted-foreground">{expanded ? '▲' : '▼'}</span>
      </button>

      {expanded && (
        <div className="border-t p-4 space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Title
              {overrides.title && (
                <span className="ml-2 rounded bg-amber-100 px-1 py-0.5 text-xs text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
                  вручную
                </span>
              )}
            </label>
            <input
              type="text"
              value={meta.title ?? ''}
              maxLength={70}
              onChange={(e) => handleChange('title', e.target.value)}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              placeholder="Авто-заполняется из H1"
            />
            <p className={`mt-1 text-xs ${(meta.title?.length ?? 0) > 60 ? 'text-destructive' : 'text-muted-foreground'}`}>
              {meta.title?.length ?? 0}/60
            </p>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Description
              {overrides.description && (
                <span className="ml-2 rounded bg-amber-100 px-1 py-0.5 text-xs text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
                  вручную
                </span>
              )}
            </label>
            <textarea
              value={meta.description ?? ''}
              maxLength={170}
              rows={3}
              onChange={(e) => handleChange('description', e.target.value)}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              placeholder="Авто-заполняется из первого абзаца"
            />
            <p className={`mt-1 text-xs ${(meta.description?.length ?? 0) > 160 ? 'text-destructive' : 'text-muted-foreground'}`}>
              {meta.description?.length ?? 0}/160
            </p>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Canonical URL
            </label>
            <input
              type="url"
              value={meta.canonical ?? ''}
              onChange={(e) => handleChange('canonical', e.target.value)}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              placeholder="https://example.com/page"
            />
          </div>

          <div className="flex justify-end">
            <button
              type="button"
              onClick={onSave}
              disabled={isSaving}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {isSaving ? 'Сохранение...' : 'Сохранить meta'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
