import type { CustomerProfile } from "../../api/types"

/** "Pastel spectrum": one pastel per rank, red at the top through pink. */
const SPECTRUM = [
  "#fecdd3", // rose
  "#fed7aa", // peach
  "#fef08a", // butter
  "#d9f99d", // lime
  "#bbf7d0", // mint
  "#bfdbfe", // sky
  "#ddd6fe", // lavender
  "#f5d0fe", // orchid
  "#fbcfe8", // pink
]

/** Daily Distance Trajectory: avg km/day/vehicle per customer as soft
 * gradient bars in rounded tracks, highest first. */
export function DailyDistanceBar({ customers }: { customers: CustomerProfile[] }) {
  const sorted = [...customers].sort((a, b) => b.avg_km_day_per_veh - a.avg_km_day_per_veh)
  const max = Math.max(1, ...sorted.map((c) => c.avg_km_day_per_veh))

  return (
    <div className="ddt">
      <div className="ddt-sub">Avg km / day / vehicle</div>
      <div className="ddt-rows">
        {sorted.map((c, i) => {
          const color = SPECTRUM[i % SPECTRUM.length]
          const pct = Math.max(4, (c.avg_km_day_per_veh / max) * 97)
          return (
            <div key={c.customer} className="ddt-row" title={`${c.customer}: ${Math.round(c.avg_km_day_per_veh)} km/d`}>
              <div
                className="ddt-bar"
                style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${color}00 0%, ${color} 100%)` }}
              />
              <span className="ddt-name">{c.customer}</span>
              <span className="ddt-value">
                <b>{Math.round(c.avg_km_day_per_veh).toLocaleString("en-IN")}</b> km/d
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
