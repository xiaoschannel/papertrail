import { useLayoutEffect, useState, type RefObject } from 'react'

/**
 * Number of columns a CSS grid actually laid out, kept current on resize.
 *
 * Reads the resolved `grid-template-columns` (one px length per track) instead of
 * re-deriving it from widths in JS, so the grid's own CSS — e.g.
 * `repeat(auto-fill, minmax(132px, 1fr))` — stays the single source of truth.
 * With `auto-fill`, empty tracks are kept, so this is the columns that FIT, not
 * the number of items.
 *
 * Measured in a layout effect, so the first paint already uses the real count.
 */
export function useGridColumnCount(ref: RefObject<HTMLElement | null>, fallback = 1): number {
  const [count, setCount] = useState(fallback)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const measure = () => {
      const tracks = getComputedStyle(el).gridTemplateColumns.split(' ').filter(Boolean).length
      setCount(tracks > 0 ? tracks : fallback)
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [ref, fallback])

  return count
}
