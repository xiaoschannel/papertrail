import { useCallback, useState, type ReactNode } from 'react'
import type { Trim, TopPoints } from '../api/types.ts'
import './TrimmedImage.css'

/** The whole page: what a page without a trim keeps. */
export const WHOLE: Trim = { top: 0, bottom: 1 }

/** A page's trim once its file is turned upright from `top` (mirror of models.turned_trim): upside down
 *  the band flips; a quarter turn would lay it across the page, so the page is left whole. */
export function turnedTrim(trim: Trim | null, top: TopPoints | ''): Trim | null {
  if (trim === null || top === '') return trim
  return top === 'down' ? { top: 1 - trim.bottom, bottom: 1 - trim.top } : null
}

type Size = { src: string; width: number; height: number }

/** A transparent image of the given proportions: a real replaced element, so the box sized from it behaves
 *  like an image under max-width, max-height and height: auto, while costing no pixels. */
const sizer = (width: number, height: number) =>
  `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"/>`)}`

/**
 * A page scan showing only the band its trim keeps, with a soft shadow on each edge that was cut: the one
 * display every scan in the app goes through, so a trim shows the same everywhere.
 *
 * The band is cut in the browser from the whole scan: the box takes the band's proportions from a sizer
 * image (see `sizer`) and the scan is placed inside it, shifted up past the cut. That needs the scan's own
 * size, so a trimmed scan stays invisible until it has loaded (the parts cut away never flash up). A scan
 * that isn't trimmed or capped is just the image, as it always was.
 *
 * `pretrimmed` is for an image that is already the band (the Workshop's server-rendered preview): it is
 * shown as it is, and `trim` only says which edges were cut.
 *
 * `capRatio` caps the height at that multiple of the width, cropping from the bottom with a short fade
 * (the gallery cards); before the scan loads, the box reserves the full cap, since receipts are tall.
 *
 * `fit="contain"` fits the scan inside its container's width and `--scan-max-h`, like an image with
 * max-width and max-height (the File Index tiles); the default fills the container's width.
 *
 * `children` are drawn over the band, positioned in percentages of it (the OCR boxes).
 */
export function TrimmedImage({
  src, alt = '', trim = null, pretrimmed = false, capRatio, fit = 'width', hidden = false, lazy = true, className = '',
  children,
}: {
  src: string
  alt?: string
  trim?: Trim | null | undefined
  pretrimmed?: boolean
  capRatio?: number | undefined
  fit?: 'width' | 'contain'
  hidden?: boolean
  lazy?: boolean
  className?: string
  children?: ReactNode
}) {
  const [measured, setMeasured] = useState<Size | null>(null)
  const measure = useCallback((img: HTMLImageElement) => {
    if (img.naturalWidth > 0) setMeasured({ src, width: img.naturalWidth, height: img.naturalHeight })
  }, [src])
  // A cached scan can finish loading before onLoad is attached: check on mount too.
  const checkIfLoaded = useCallback((img: HTMLImageElement | null) => {
    if (img?.complete) measure(img)
  }, [measure])

  const shown = trim ?? WHOLE
  const band = pretrimmed ? WHOLE : shown           // the part of the image to cut out here
  const size = measured?.src === src ? measured : null
  const trimmed = band.top > 0 || band.bottom < 1
  const bandHeight = size ? size.height * (band.bottom - band.top) : 0
  const capped = size !== null && capRatio !== undefined && bandHeight / size.width > capRatio + 0.01
  const classes = ['trimmed-scan', `trimmed-scan--${fit}`, className,
    shown.top > 0 && 'is-cut-top', shown.bottom < 1 && !capped && 'is-cut-bottom', capped && 'is-capped']
    .filter(Boolean).join(' ')
  const img = { src, alt, loading: lazy ? 'lazy' as const : undefined, draggable: false, onLoad: (e: { currentTarget: HTMLImageElement }) => measure(e.currentTarget) }

  // Nothing to cut: the image itself, as every scan was shown before trims.
  if (!trimmed && capRatio === undefined) {
    return (
      <div className={classes} hidden={hidden}>
        <img {...img} ref={checkIfLoaded} className="trimmed-scan__plain" />
        {children}
      </div>
    )
  }

  if (!size) {
    // Not loaded yet. A capped card reserves its cap and shows the top of the scan as it arrives; a
    // trimmed scan waits, unseen, since its size decides where the cut falls.
    return (
      <div className={classes} hidden={hidden}>
        {capRatio !== undefined
          ? <img className="trimmed-scan__sizer" src={sizer(100, Math.round(100 * capRatio))} alt="" aria-hidden />
          : null}
        <div className={capRatio !== undefined ? 'trimmed-scan__clip' : undefined}>
          <img {...img} ref={checkIfLoaded}
            className={capRatio !== undefined ? 'trimmed-scan__top' : 'trimmed-scan__waiting'} />
        </div>
      </div>
    )
  }

  const shownHeight = capped ? size.width * capRatio! : bandHeight
  const pct = (value: number) => `${(value / shownHeight) * 100}%`
  return (
    <div className={classes} hidden={hidden}>
      <img className="trimmed-scan__sizer" src={sizer(size.width, Math.max(1, Math.round(shownHeight)))} alt="" aria-hidden />
      <div className="trimmed-scan__clip">
        <img {...img} ref={checkIfLoaded} className="trimmed-scan__scan"
          style={{ top: pct(-band.top * size.height), height: pct(size.height) }} />
      </div>
      {children}
    </div>
  )
}
