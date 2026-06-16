'use client'

import { generateHTML } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import DOMPurify from 'dompurify'
import { useMemo } from 'react'

const EXTENSIONS = [StarterKit]

// StarterKit doesn't include arbitrary HTML extensions, but we sanitize
// as defense-in-depth in case a compromised CMS user stores malicious content.
const ALLOWED_TAGS = ['p', 'br', 'strong', 'em', 's', 'u', 'code', 'pre', 'blockquote',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'hr']

interface TiptapRendererProps {
  content: Record<string, unknown>
  className?: string
}

export function TiptapRenderer({ content, className }: TiptapRendererProps) {
  const html = useMemo(() => {
    if (!content || !content.type) return ''
    try {
      const raw = generateHTML(content as Parameters<typeof generateHTML>[0], EXTENSIONS)
      return DOMPurify.sanitize(raw, { ALLOWED_TAGS, ALLOWED_ATTR: [] })
    } catch {
      return ''
    }
  }, [content])

  if (!html) return null

  return (
    <div
      className={className}
      // sanitized via DOMPurify with allowlist of safe formatting tags
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
