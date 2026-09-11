import Plot from "../plotly-shim"
import type { VehicleRecord } from "../api/types"

/** Horizontal top-5 / bottom-5 bar chart by avg_km_per_day, with a dashed
 * benchmark line at the category mean -- matches createCleanBarPlot() in the
 * original report.html. */
export function TopBottomBarChart({
  vehicles,
  isTop,
  benchmarkVal,
}: {
  vehicles: VehicleRecord[]
  isTop: boolean
  benchmarkVal: number
}) {
  const sorted = [...vehicles].sort((a, b) => b.avg_km_per_day - a.avg_km_per_day)
  const picked = (isTop ? sorted.slice(0, 5) : sorted.slice(-5).reverse()).reverse()

  return (
    <Plot
      data={[
        {
          type: "bar",
          orientation: "h",
          x: picked.map((v) => v.avg_km_per_day),
          y: picked.map((v) => v.plate),
          text: picked.map((v) => `${Math.round(v.avg_km_per_day)} km/d`),
          textposition: "outside",
          marker: { color: isTop ? "#5cb030" : "#ef4444" },
        },
      ]}
      layout={{
        margin: { l: 100, r: 30, t: 10, b: 30 },
        height: 260,
        xaxis: { title: { text: "Avg km/day" } },
        shapes: [
          {
            type: "line",
            x0: benchmarkVal,
            x1: benchmarkVal,
            y0: -0.5,
            y1: 4.5,
            line: { color: "#94a3b8", width: 1.5, dash: "dash" },
          },
        ],
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        font: { color: "var(--text-secondary)" },
      }}
      config={{ responsive: true, displayModeBar: false }}
      style={{ width: "100%" }}
      useResizeHandler
    />
  )
}
