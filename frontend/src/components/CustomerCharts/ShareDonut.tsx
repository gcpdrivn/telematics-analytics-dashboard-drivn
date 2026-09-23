import Plot from "../../plotly-shim"
import type { CustomerName, CustomerProfile } from "../../api/types"
import { CUSTOMER_COLORS } from "./colors"

function dim(hex: string): string {
  const c = hex.replace("#", "")
  const r = parseInt(c.substring(0, 2), 16)
  const g = parseInt(c.substring(2, 4), 16)
  const b = parseInt(c.substring(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, 0.35)`
}

/** Filtering this donut down to one customer would make it a degenerate
 * 100% slice, so the page-level customer filter highlights (pulls out) the
 * selected slice here instead of filtering the underlying data. */
export function ShareDonut({
  customers,
  highlight,
}: {
  customers: CustomerProfile[]
  highlight?: CustomerName | null
}) {
  return (
    <Plot
      data={[
        {
          type: "pie",
          hole: 0.62,
          labels: customers.map((c) => c.customer),
          values: customers.map((c) => c.share_pct),
          marker: {
            colors: customers.map((c) =>
              highlight && c.customer !== highlight ? dim(CUSTOMER_COLORS[c.customer]) : CUSTOMER_COLORS[c.customer]
            ),
          },
          pull: highlight ? customers.map((c) => (c.customer === highlight ? 0.08 : 0)) : undefined,
          textinfo: "percent",
          hoverinfo: "label+percent",
          automargin: true,
        },
      ]}
      layout={{
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        margin: { l: 10, r: 10, t: 10, b: 50 },
        height: 300,
        showlegend: true,
        legend: { orientation: "h", x: 0.5, xanchor: "center", y: -0.15 },
      }}
      config={{ responsive: true, displayModeBar: false }}
      style={{ width: "100%" }}
      useResizeHandler
    />
  )
}
