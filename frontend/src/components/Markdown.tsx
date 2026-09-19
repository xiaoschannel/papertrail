import { useMemo } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

/**
 * OCR output rendered as markdown, the way Streamlit showed it: grounding models return tables and
 * headings, which are far easier to check against the scan than a raw dump. The text comes from a
 * model reading someone's receipt, so it is sanitized before it reaches the DOM.
 */
export function Markdown({ source, className = '' }: { source: string; className?: string }) {
  const html = useMemo(
    () => DOMPurify.sanitize(marked.parse(source, { async: false, breaks: true, gfm: true }), {
      // Nothing here should be able to restyle the app, fetch from the network or fake a form.
      FORBID_TAGS: ['style', 'form', 'input', 'button', 'select', 'textarea', 'img'],
      FORBID_ATTR: ['style'],
    }),
    [source],
  )
  return <div className={`markdown ${className}`} dangerouslySetInnerHTML={{ __html: html }} />
}
