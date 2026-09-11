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
        // Plotly's default already leaves a gap in the line wherever y is
        // null -- that gap IS the "device reported nothing this day"
        // signal (a transmission problem), distinct from a point sitting
        // at 0 (device reported fine, vehicle just didn't move that day).
        connectgaps: false,
        line: { width: 2.2 },
        marker: { size: 5 },
        text: data.dates.map((d, i) => {
          const dist = v.distances[i]
          if (dist === null) return `${d}<br>No data received`
          const gps = v.gps_disconnections[i]
          const gpsNote =
            gps !== null && gps > 0
              ? `<br>⚠️ ${gps} GPS disconnection${gps > 1 ? "s" : ""}`
              : ""
          return `${d}<br>${dist} km${gpsNote}`
        }),
        hoverinfo: "text" as const,
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
