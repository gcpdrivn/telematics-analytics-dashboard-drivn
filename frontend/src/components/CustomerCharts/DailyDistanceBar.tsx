import Plot from "../../plotly-shim"
import type { CustomerProfile } from "../../api/types"
import { CUSTOMER_COLORS, PLOT_LAYOUT_BASE } from "./colors"

export function DailyDistanceBar({ customers }: { customers: CustomerProfile[] }) {
  const sorted = [...customers].sort((a, b) => b.avg_km_day_per_veh - a.avg_km_day_per_veh)
  return (
    <Plot
      data={[
        {
          type: "bar",
          orientation: "h",
          x: sorted.map((c) => c.avg_km_day_per_veh),
          y: sorted.map((c) => c.customer),
          text: sorted.map((c) => `${Math.round(c.avg_km_day_per_veh)} km/d`),
          textposition: "outside",
          marker: { color: sorted.map((c) => CUSTOMER_COLORS[c.customer]) },
        },
      ]}
      layout={{ ...PLOT_LAYOUT_BASE, height: 220, xaxis: { title: { text: "Avg km/day/vehicle" } } }}
      config={{ responsive: true, displayModeBar: false }}
      style={{ width: "100%" }}
      useResizeHandler
    />
  )
}
