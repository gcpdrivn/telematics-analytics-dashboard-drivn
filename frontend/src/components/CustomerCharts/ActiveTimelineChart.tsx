import Plot from "../../plotly-shim"
import type { ActiveTimeline, CustomerName } from "../../api/types"
import { CUSTOMER_COLORS, PLOT_LAYOUT_BASE } from "./colors"

const CUSTOMERS: CustomerName[] = ["FreshBus", "ZingBus", "BillionE"]

export function ActiveTimelineChart({ timeline }: { timeline: ActiveTimeline }) {
  return (
    <Plot
      data={CUSTOMERS.map((c) => ({
        type: "scatter" as const,
        mode: "lines+markers" as const,
        name: c,
        x: timeline.dates,
        y: timeline[c].pcts,
        line: { color: CUSTOMER_COLORS[c] },
        marker: { color: CUSTOMER_COLORS[c], size: 5 },
        text: timeline[c].counts.map(
          (cnt, i) => `${cnt}/${timeline[c].fleet_sizes[i]} active`
        ),
        hoverinfo: "text+name+x",
      }))}
      layout={{
        ...PLOT_LAYOUT_BASE,
        height: 300,
        yaxis: { title: { text: "Active %" }, range: [0, 105], ticksuffix: "%" },
      }}
      config={{ responsive: true, displayModeBar: false }}
      style={{ width: "100%" }}
      useResizeHandler
    />
  )
}
