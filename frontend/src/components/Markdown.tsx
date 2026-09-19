import { useMemo } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const MARKDOWN_TAGS = [
  'p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'blockquote', 'pre', 'code',
  'em', 'strong', 'del', 'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
]

/**
 * OCR output rendered as markdown: grounding models return tables and
 * headings, which are far easier to check against the scan than a raw dump. The text comes from a
 * model reading someone's receipt, so it is sanitized before it reaches the DOM.
 */
export function Markdown({ source, className = '' }: { source: string; className?: string }) {
  const html = useMemo(
    () => DOMPurify.sanitize(marked.parse(source, { async: false, breaks: true, gfm: true }), {
      // Only what markdown itself produces: nothing that can restyle the app (class, style), fetch from
      // the network (img, video, src, poster), or fake a control.
      ALLOWED_TAGS: MARKDOWN_TAGS,
      ALLOWED_ATTR: ['href', 'title', 'align', 'colspan', 'rowspan', 'start'],
    }),
    [source],
  )
  return <div className={`markdown ${className}`} dangerouslySetInnerHTML={{ __html: html }} />
}
