import type { KpiSummary } from "../api/types"

function formatIndian(val: number, decimals = 0): string {
  if (val === null || val === undefined || Number.isNaN(val)) return "-"
  const isNeg = val < 0
  val = Math.abs(val)
  const fixed = val.toFixed(decimals)
  const [intPart, decPart] = fixed.split(".")
  let res: string
  if (intPart.length <= 3) {
    res = intPart
  } else {
    const last3 = intPart.slice(-3)
    let rem = intPart.slice(0, -3)
    const groups: string[] = []
    while (rem.length > 2) {
      groups.unshift(rem.slice(-2))
      rem = rem.slice(0, -2)
    }
    if (rem) groups.unshift(rem)
    res = groups.join(",") + "," + last3
  }
  return (isNeg ? "-" : "") + res + (decPart ? "." + decPart : "")
}

export function KpiCards({ kpi }: { kpi: KpiSummary }) {
  return (
    <div className="kpi-row">
      <div className="kpi-box">
        <div className="kpi-label">{kpi.dist_label}</div>
        <div className="kpi-num">
          {formatIndian(kpi.daily_distance)} <span className="kpi-unit">km</span>
        </div>
        <div className="kpi-sub">
          ⚡ {formatIndian(kpi.per_veh_daily_dist)} {kpi.dist_unit_sub}
        </div>
        <div className="kpi-prior">Total: {formatIndian(kpi.total_distance)} km</div>
      </div>

      <div className="kpi-box">
        <div className="kpi-label">{kpi.hours_label}</div>
        <div className="kpi-num">
          {formatIndian(kpi.daily_hours, 1)} <span className="kpi-unit">hrs</span>
        </div>
        <div className="kpi-sub">
          ⏱️ {formatIndian(kpi.per_veh_daily_hrs, 1)} {kpi.hours_unit_sub}
        </div>
        <div className="kpi-prior">Total: {formatIndian(kpi.total_hours, 1)} hrs</div>
      </div>

      <div className="kpi-box">
        <div className="kpi-label">{kpi.fleet_label}</div>
        <div className="kpi-num">{kpi.total_vehicles}</div>
        <div className="kpi-sub">{kpi.fleet_breakdown}</div>
        <div className="kpi-prior">{kpi.fleet_prior}</div>
      </div>

      <div className="kpi-box">
        <div className="kpi-label">{kpi.peak_label}</div>
        <div className="kpi-num" style={{ fontSize: "1.4rem" }}>
          {kpi.peak_num}
        </div>
        <div className="kpi-sub">{kpi.peak_sub}</div>
        <div className="kpi-prior">{kpi.peak_prior}</div>
      </div>
    </div>
  )
}
