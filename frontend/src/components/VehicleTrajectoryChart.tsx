import Plot from "../plotly-shim"
import type { TrajectoryResponse } from "../api/types"

export function VehicleTrajectoryChart({ data }: { data: TrajectoryResponse }) {
  return (
    <Plot
      data={data.vehicles.map((v) => ({
        type: "scatter" as const,
        mode: "lines+markers" as const,
        name: `${v.plate} (${v.soc_str})`,
        x: data.dates,
        y: v.distances,
        line: { width: 2.2 },
        marker: { size: 5 },
      }))}
      layout={{
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        margin: { l: 60, r: 20, t: 20, b: 60 },
        height: 340,
        // Same fix as ActiveTimelineChart: Plotly treats this as a date axis
        // and sorts it chronologically regardless of array order, so the
        // backend already sending `dates` latest-first doesn't control the
        // rendered direction -- only reversing the axis itself does.
        xaxis: { autorange: "reversed" },
        yaxis: { title: { text: "Daily Distance Covered (km)" } },
        legend: { orientation: "h", y: -0.25 },
      }}
      config={{ responsive: true, displayModeBar: false }}
      style={{ width: "100%" }}
      useResizeHandler
    />
  )
}
