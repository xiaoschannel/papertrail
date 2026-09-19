import { useEffect, useState } from 'react'

/**
 * A value that settles before anyone acts on it. Sliders fire a change per step, and on these pages
 * each step would re-render a scan on the server or re-cluster every merchant name.
 */
export function useDebounced<T>(value: T, delayMs: number): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return settled
}
