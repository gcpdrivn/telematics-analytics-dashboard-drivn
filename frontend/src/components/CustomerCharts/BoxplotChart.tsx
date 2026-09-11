import Plot from "../../plotly-shim"
import type { CustomerName } from "../../api/types"
import { CUSTOMER_COLORS, PLOT_LAYOUT_BASE } from "./colors"

export function BoxplotChart({ boxData }: { boxData: Record<CustomerName, number[]> }) {
  const customers = Object.keys(boxData) as CustomerName[]
  return (
    <Plot
      data={customers.map((c) => ({
        type: "box" as const,
        name: c,
        y: boxData[c],
        marker: { color: CUSTOMER_COLORS[c] },
        boxpoints: "outliers" as const,
        jitter: 0.28,
        pointpos: -1.6,
      }))}
      layout={{ ...PLOT_LAYOUT_BASE, height: 280, yaxis: { title: { text: "Active daily distance (km)" } }, showlegend: false }}
      config={{ responsive: true, displayModeBar: false }}
      style={{ width: "100%" }}
      useResizeHandler
    />
  )
}
