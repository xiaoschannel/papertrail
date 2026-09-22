/*
 * The Config page's picture of the tilt threshold: a short, a typical and a long receipt, each drawn to scale
 * at the smallest tilt that counts at the chosen share, with the wedge that tilt leaves along its side; and
 * each one's top corner enlarged, where the wedge is widest.
 */

/** The receipts drawn, by their length over their width. */
const RECEIPTS = [
  { name: 'Short receipt', ratio: 1.6 },
  { name: 'Typical receipt', ratio: 2.8 },
  { name: 'Long receipt', ratio: 5 },
]
const WIDTH = 64            // each receipt's width in the picture, px
const LINE_GAP = 11
/** A receipt's real width, for saying how wide the wedge is on paper. */
const PAPER_MM = 80
/** How much the corner view enlarges, and how much of the receipt it shows (px of the picture). */
const ZOOM = 5
const CORNER = 22

/** The smallest tilt that counts on a page ``ratio`` times as long as wide (deskew's rule), in degrees, and
 *  whether the floor (too small to measure) is what decides it. */
export function smallestTilt(share: number, ratio: number, minDegrees: number, maxDegrees: number) {
  const angle = (Math.asin(Math.min(1, share / ratio)) * 180) / Math.PI
  return { degrees: Math.min(maxDegrees, Math.max(minDegrees, angle)), floored: angle < minDegrees }
}

function Receipt({ ratio, degrees, x0, y0 }: { ratio: number; degrees: number; x0: number; y0: number }) {
  const h = WIDTH * ratio
  const theta = (degrees * Math.PI) / 180
  const lean = h * Math.sin(theta)
  const lines = Math.floor((h - 18) / LINE_GAP)
  return (
    <>
      {/* the empty wedge the tilt leaves along the side, between the upright line and the paper's edge */}
      <polygon className="tilt-demo__wedge" points={`${x0},${y0 + h} ${x0},${y0} ${x0 + lean},${y0 + h - h * Math.cos(theta)}`} />
      <g transform={`translate(${x0} ${y0 + h}) rotate(${degrees})`}>
        <rect className="tilt-demo__paper" x={0} y={-h} width={WIDTH} height={h} />
        {Array.from({ length: lines }, (_, i) => (
          <rect key={i} className="tilt-demo__ink" x={6} y={-h + 8 + i * LINE_GAP}
            width={i % 3 === 0 ? WIDTH - 12 : WIDTH * (0.45 + ((i * 37) % 30) / 100)} height={3} />
        ))}
      </g>
      <line className="tilt-demo__plumb" x1={x0} y1={y0 - 6} x2={x0} y2={y0 + h} />
    </>
  )
}

export function TiltDemo({ share, minDegrees, maxDegrees }: { share: number; minDegrees: number; maxDegrees: number }) {
  return (
    <div className="tilt-demo">
      {RECEIPTS.map(({ name, ratio }) => {
        const { degrees, floored } = smallestTilt(share, ratio, minDegrees, maxDegrees)
        const h = WIDTH * ratio
        const lean = h * Math.sin((degrees * Math.PI) / 180)
        const pad = 8
        const x0 = pad
        const y0 = pad
        const corner = `${x0 - 6} ${y0 - 6} ${CORNER + lean + 6} ${CORNER + 6}`
        return (
          <figure key={name}>
            <div className="tilt-demo__pictures">
              <svg width={WIDTH + lean + 2 * pad} height={h + 2 * pad} role="img"
                aria-label={`${name} tilted ${degrees.toFixed(2)} degrees, to scale`}>
                <Receipt ratio={ratio} degrees={degrees} x0={x0} y0={y0} />
              </svg>
              <svg className="tilt-demo__corner" viewBox={corner} width={(CORNER + lean + 6) * ZOOM}
                height={(CORNER + 6) * ZOOM} role="img" aria-label={`${name}'s top corner, ${ZOOM} times larger`}>
                <Receipt ratio={ratio} degrees={degrees} x0={x0} y0={y0} />
              </svg>
            </div>
            <figcaption>
              <strong>{name}</strong>
              <span>{ratio}× as long as wide</span>
              <span>counts from {degrees.toFixed(2)}°{floored ? ' (the floor)' : ''}</span>
            </figcaption>
          </figure>
        )
      })}
      <p className="config-note tilt-demo__note">
        Each receipt to scale at the smallest tilt that counts, and its top corner {ZOOM}× larger. At this setting
        each one counts once its wedge is {(share * PAPER_MM).toFixed(1)} mm across on an {PAPER_MM} mm wide
        receipt, whatever its length.
      </p>
    </div>
  )
}
