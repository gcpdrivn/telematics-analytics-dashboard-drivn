/** Ports make_sparkline_svg() from Analysis/generate_presentation_report.py --
 * same geometry, computed client-side from the raw distance history instead
 * of a baked SVG string from the backend. */
export function Sparkline({
  values,
  width = 96,
  height = 22,
}: {
  values: number[]
  width?: number
  height?: number
}) {
  const maxVal = values.length ? Math.max(...values) : 0

  if (!values.length || maxVal === 0) {
    return (
      <svg width={width} height={height} className="sparkline-svg">
        <line
          x1={0}
          y1={height / 2}
          x2={width}
          y2={height / 2}
          stroke="#94a3b8"
          strokeWidth={1.5}
          strokeDasharray="2,2"
        />
      </svg>
    )
  }

  const n = values.length
  const dx = n > 1 ? (width - 4) / (n - 1) : width
  const pts = values.map((v, i) => {
    const x = 2 + i * dx
    const y = height - 3 - (v / maxVal) * (height - 6)
    return [x, y] as const
  })

  const polyline = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")
  const areaPts = `${pts[0][0].toFixed(1)},${height - 1} ${polyline} ${pts[pts.length - 1][0].toFixed(1)},${height - 1}`
  const gradId = `spk-grad-${width}x${height}`

  return (
    <svg width={width} height={height} className="sparkline-svg" viewBox={`0 0 ${width} ${height}`}>
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--accent-brand)" stopOpacity="0.35" />
          <stop offset="100%" stopColor="var(--accent-brand)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon fill={`url(#${gradId})`} points={areaPts} />
      <polyline
        fill="none"
        stroke="var(--accent-brand)"
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
        points={polyline}
      />
    </svg>
  )
}
