import type { CustomerName, CustomerProfile } from "../../api/types"

/** "Vivid on warm paper" palette for this card only (the design's own
 * colours); other charts keep CUSTOMER_COLORS. */
const DONUT_COLORS: Record<CustomerName, string> = {
  FreshBus: "#5cb030",
  ZingBus: "#2f7fe0",
  BillionE: "#e07b12",
  "AVG LOGISTICS": "#3f3f46",
  "Purple Mobility": "#a855f7",
  SWITCHLABS: "#dc2626",
  Enviiirowheels: "#0e9aa7",
  Flytta: "#ec4899",
  Juleh: "#94a3b8",
}

/** Value colours: the slice colour, darkened where it is too light to read. */
const VALUE_COLORS: Partial<Record<CustomerName, string>> = {
  FreshBus: "#3f7f1d",
  BillionE: "#b45309",
  Enviiirowheels: "#0b7c86",
  Flytta: "#db2777",
  Juleh: "#64748b",
}

function fmtPct(v: number): string {
  if (v >= 10) return `${v.toFixed(1)}%`
  if (v >= 1) return `${v.toFixed(2)}%`
  return `${v.toFixed(1)}%`
}

const CX = 100
const CY = 100
const R_OUT = 92
const R_IN = 58

function point(r: number, angle: number): [number, number] {
  return [CX + r * Math.cos(angle), CY + r * Math.sin(angle)]
}

function slicePath(a0: number, a1: number): string {
  const large = a1 - a0 > Math.PI ? 1 : 0
  const [x0, y0] = point(R_OUT, a0)
  const [x1, y1] = point(R_OUT, a1)
  const [x2, y2] = point(R_IN, a1)
  const [x3, y3] = point(R_IN, a0)
  return `M${x0},${y0} A${R_OUT},${R_OUT} 0 ${large} 1 ${x1},${y1} L${x2},${y2} A${R_IN},${R_IN} 0 ${large} 0 ${x3},${y3} Z`
}

/** Fleet Distance Share: a donut beside a ranked legend. The page-level
 * customer filter highlights (pulls out) the selected slice instead of
 * filtering, which would leave a single 100% slice. */
export function ShareDonut({
  customers,
  highlight,
}: {
  customers: CustomerProfile[]
  highlight?: CustomerName | null
}) {
  const rows = [...customers].filter((c) => c.share_pct > 0).sort((a, b) => b.share_pct - a.share_pct)
  const total = rows.reduce((s, c) => s + c.share_pct, 0) || 1

  let angle = -Math.PI / 2
  const slices = rows.map((c) => {
    const sweep = (c.share_pct / total) * 2 * Math.PI
    const a0 = angle
    angle += sweep
    return { c, a0, a1: angle, mid: a0 + sweep / 2 }
  })

  return (
    <div className="donut-card">
      <div className="donut-head">
        <span className="donut-title">Fleet Distance Share</span>
        <span className="donut-badge">{rows.length} customers</span>
      </div>
      <div className="donut-body">
        <svg className="donut-svg" viewBox="0 0 200 200" role="img" aria-label="Fleet distance share by customer">
          {slices.map(({ c, a0, a1, mid }) => {
            const on = !highlight || c.customer === highlight
            const pull = highlight && c.customer === highlight ? 6 : 0
            const d =
              a1 - a0 >= 2 * Math.PI - 1e-6
                ? `M${CX},${CY - R_OUT} A${R_OUT},${R_OUT} 0 1 1 ${CX - 0.01},${CY - R_OUT} Z M${CX},${CY - R_IN} A${R_IN},${R_IN} 0 1 0 ${CX + 0.01},${CY - R_IN} Z`
                : slicePath(a0, a1)
            return (
              <path
                key={c.customer}
                d={d}
                fill={DONUT_COLORS[c.customer]}
                className="donut-slice"
                opacity={on ? 1 : 0.3}
                transform={pull ? `translate(${pull * Math.cos(mid)},${pull * Math.sin(mid)})` : undefined}
              >
                <title>{`${c.customer}: ${fmtPct(c.share_pct)}`}</title>
              </path>
            )
          })}
        </svg>
        <ul className="donut-legend">
          {rows.map((c) => {
            const on = !highlight || c.customer === highlight
            return (
              <li key={c.customer} className="donut-row" style={{ opacity: on ? 1 : 0.45 }}>
                <span className="donut-swatch" style={{ background: DONUT_COLORS[c.customer] }} />
                <span className="donut-name">{c.customer}</span>
                <span
                  className="donut-value"
                  style={{ color: VALUE_COLORS[c.customer] ?? DONUT_COLORS[c.customer] }}
                >
                  {fmtPct(c.share_pct)}
                </span>
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}
