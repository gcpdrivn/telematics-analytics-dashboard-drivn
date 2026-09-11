import Plot from "../../plotly-shim"
import type { CustomerName, DowProfileEntry } from "../../api/types"
import { CUSTOMER_COLORS, PLOT_LAYOUT_BASE } from "./colors"

export function SeasonalityChart({
  profiles,
  dowLabels,
}: {
  profiles: Record<CustomerName, DowProfileEntry[]>
  dowLabels: string[]
}) {
  const customers = Object.keys(profiles) as CustomerName[]
  return (
    <Plot
      data={customers.map((c) => ({
        type: "scatter" as const,
        mode: "lines+markers" as const,
        name: c,
        x: dowLabels,
        y: profiles[c].map((d) => d.mean_dist),
        error_y: { type: "data" as const, array: profiles[c].map((d) => d.std_dist), visible: true },
        line: { color: CUSTOMER_COLORS[c] },
        marker: { color: CUSTOMER_COLORS[c] },
        text: profiles[c].map(
          (d) => `Mean: ${d.mean_dist.toFixed(0)} km/d<br>±${d.std_dist.toFixed(0)} km (CV ${d.volatility_pct.toFixed(0)}%)<br>n=${d.samples}`
        ),
        hoverinfo: "text+name",
      }))}
      layout={{ ...PLOT_LAYOUT_BASE, height: 300, yaxis: { title: { text: "Mean active distance (km)" } } }}
      config={{ responsive: true, displayModeBar: false }}
      style={{ width: "100%" }}
      useResizeHandler
    />
  )
}
