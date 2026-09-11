import Plot from "../../plotly-shim"
import type { CustomerProfile } from "../../api/types"
import { CUSTOMER_COLORS } from "./colors"

export function ShareDonut({ customers }: { customers: CustomerProfile[] }) {
  return (
    <Plot
      data={[
        {
          type: "pie",
          hole: 0.62,
          labels: customers.map((c) => c.customer),
          values: customers.map((c) => c.share_pct),
          marker: { colors: customers.map((c) => CUSTOMER_COLORS[c.customer]) },
          textinfo: "label+percent",
          hoverinfo: "label+percent",
        },
      ]}
      layout={{
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        margin: { l: 10, r: 10, t: 10, b: 10 },
        height: 260,
        showlegend: false,
      }}
      config={{ responsive: true, displayModeBar: false }}
      style={{ width: "100%" }}
      useResizeHandler
    />
  )
}
